"""In-memory PHI/PII log sanitization — the privacy guardrail.

Billing data is treated as Protected Health Information (PHI). This module
guarantees that PHI never reaches a log sink (console, file, aggregator). The
design is **defense-in-depth, deny-list-first** — the deterministic layers run
on every record; the ML layer is an optional backstop:

    Layer 0  Structured-logging discipline (enforced by call sites)
             We log only known-safe scalars (counts, statuses, opaque ids).
             The filter below is a *backstop*, not a license to log raw PHI.

    Layer 1  Exact-token deny-list   ── the hard guarantee
             The specific PHI strings observed at runtime (customer name,
             product names, invoice numbers) are registered and removed by an
             O(1)-per-token compiled regex. This is the layer that *guarantees*
             a given name can never appear in a log line — ML cannot promise
             that (Presidio PERSON recall is ~0.5 on noisy text).

    Layer 2  Fast structured-PII regexes  (email, phone, SSN, card, IP, URL,
             Odoo invoice numbers) — deterministic, microsecond-cheap.

    Layer 3  Presidio NER  (optional, lazy, ``en_core_web_sm``, PERSON/LOCATION)
             A context-aware sweep over residual free text. Off by default
             because spaCy NER is comparatively heavy; when enabled it is
             initialised once and cached. Failure to load degrades *open* to
             Layers 1–2 (it never raises into the logging path).

Critical correctness detail
---------------------------
Python ``logging`` renders the final string lazily at emit time via
``record.getMessage() == record.msg % record.args``. Scrubbing only
``record.msg`` would leave PHI in ``record.args`` (e.g.
``log.info("customer %s", name)``). The filter therefore renders the message
*once*, scrubs the rendered text, and then **clears ``record.args``** so no
later re-formatting can resurrect the original values. Exception/stack text
(which bypasses the ``msg % args`` path) is scrubbed too.

The filter is attached to *handlers*, not loggers, so redaction happens at the
emit boundary and guards every sink uniformly — including records propagated
from third-party libraries.
"""
from __future__ import annotations

import logging
import re
import threading
import traceback
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# Redaction placeholders
# --------------------------------------------------------------------------- #
_TOKEN = "[REDACTED:PHI]"          # runtime-registered exact PHI tokens
_NER = "[REDACTED:NER]"            # Presidio-detected entities
_PLACEHOLDERS = {
    "EMAIL": "[REDACTED:EMAIL]",
    "PHONE": "[REDACTED:PHONE]",
    "SSN": "[REDACTED:SSN]",
    "CREDIT_CARD": "[REDACTED:CARD]",
    "IP": "[REDACTED:IP]",
    "URL": "[REDACTED:URL]",
    "INVOICE_NO": "[REDACTED:INVOICE_NO]",
}

# --------------------------------------------------------------------------- #
# Layer 2 — deterministic structured-PII patterns
# Ordered: the most specific patterns run first so they win over broader ones.
# --------------------------------------------------------------------------- #
_REGEX_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # Odoo invoice references, e.g. INV/2026/00003, INV-2026-00003, RINV/2026/0001
    ("INVOICE_NO", re.compile(r"\b[A-Z]{1,5}[/\-]\d{4}[/\-]\d{3,}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Card-like: 13–19 digits, optionally split into 4-digit groups by space/dash
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("URL", re.compile(r"\bhttps?://[^\s<>\"')]+", re.IGNORECASE)),
    # Phone numbers — deliberately CONSERVATIVE: require a '+' prefix or a
    # parenthesized area code, so we never clobber dates, hashes, ISO
    # timestamps, or ordinary digit runs that appear in operational logs.
    ("PHONE", re.compile(r"(?<![\w])\+\d[\d .\-]{6,}\d(?![\w])")),
    ("PHONE", re.compile(r"\(\d{2,4}\)[ .\-]?\d{2,4}[ .\-]?\d{2,4}")),
]

# Patterns that describe infrastructure as well as PHI; matches equal to an
# operational allow-listed value (e.g. the configured instance endpoint) are
# preserved so logs stay debuggable. Personal URLs/IPs in free text are still
# redacted.
_ALLOWLISTABLE = {"URL", "IP"}


class PhiRedactor:
    """Thread-safe, in-memory redaction engine.

    Instances are cheap to create; the (optional) Presidio engine is built
    lazily on first use and cached. ``register_secret`` may be called at any
    time from any thread as PHI values are discovered during a run.
    """

    def __init__(
        self,
        *,
        enable_presidio: bool = False,
        presidio_entities: Iterable[str] = ("PERSON", "LOCATION"),
        spacy_model: str = "en_core_web_sm",
        min_token_len: int = 3,
        allow: Iterable[str] = (),
    ) -> None:
        self._lock = threading.RLock()
        self._secrets: set[str] = set()
        self._secret_re: Optional[re.Pattern] = None
        self._min_token_len = max(1, int(min_token_len))
        self._allow: set[str] = {a for a in allow if a}

        self._enable_presidio = bool(enable_presidio)
        self._presidio_entities = tuple(presidio_entities)
        self._spacy_model = spacy_model
        self._analyzer = None
        self._anonymizer = None
        self._presidio_failed = False

    # -- deny-list management (Layer 1) ------------------------------------ #
    def register_secret(self, value: object) -> None:
        """Register a single exact PHI token to be removed from all logs.

        Short tokens (< ``min_token_len``) are ignored to avoid catastrophic
        over-redaction (e.g. a 1-character product code blanking everything).
        """
        if value is None:
            return
        text = str(value).strip()
        if len(text) < self._min_token_len:
            return
        with self._lock:
            if text not in self._secrets:
                self._secrets.add(text)
                self._rebuild_secret_re()

    def register_secrets(self, values: Iterable[object]) -> None:
        """Register many exact PHI tokens at once (rebuilds the matcher once)."""
        dirty = False
        with self._lock:
            for value in values:
                if value is None:
                    continue
                text = str(value).strip()
                if len(text) >= self._min_token_len and text not in self._secrets:
                    self._secrets.add(text)
                    dirty = True
            if dirty:
                self._rebuild_secret_re()

    def _rebuild_secret_re(self) -> None:
        """Compile one case-insensitive alternation; longest tokens first so a
        multi-word value wins over any substring of it."""
        seen: set[str] = set()
        ordered: list[str] = []
        for tok in sorted(self._secrets, key=len, reverse=True):
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(re.escape(tok))
        self._secret_re = re.compile("|".join(ordered), re.IGNORECASE) if ordered else None

    def add_allow(self, *values: object) -> None:
        """Allow-list operational (non-PHI) substrings, e.g. the instance URL,
        so they are not redacted by the URL/IP patterns."""
        with self._lock:
            for value in values:
                if value:
                    self._allow.add(str(value))

    def _is_allowed(self, text: str) -> bool:
        return any(a in text or text in a for a in self._allow)

    @property
    def secret_count(self) -> int:
        with self._lock:
            return len(self._secrets)

    # -- the scrub pipeline ------------------------------------------------ #
    def scrub(self, text: Optional[object]) -> str:
        """Return ``text`` with all detected PHI/PII replaced by placeholders.

        Never raises: any internal failure falls back to the most aggressive
        safe behaviour (returning a fully redacted marker) rather than leaking.
        """
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return text
        try:
            out = text
            # Layer 1 — exact-token deny-list (the guarantee)
            with self._lock:
                secret_re = self._secret_re
            if secret_re is not None:
                out = secret_re.sub(_TOKEN, out)
            # Layer 2 — structured PII regexes
            for label, pattern in _REGEX_PATTERNS:
                placeholder = _PLACEHOLDERS[label]
                if label in _ALLOWLISTABLE and self._allow:
                    out = pattern.sub(
                        lambda m: m.group(0) if self._is_allowed(m.group(0)) else placeholder, out
                    )
                else:
                    out = pattern.sub(placeholder, out)
            # Layer 3 — optional Presidio NER over residual free text
            if self._enable_presidio and not self._presidio_failed:
                out = self._presidio_scrub(out)
            return out
        except Exception:
            # Fail closed: it is always safer to over-redact than to leak.
            return "[REDACTION-ERROR]"

    # -- Presidio (Layer 3) ------------------------------------------------ #
    def _ensure_presidio(self) -> None:
        if self._analyzer is not None or self._presidio_failed:
            return
        with self._lock:
            if self._analyzer is not None or self._presidio_failed:
                return
            try:
                from presidio_analyzer import AnalyzerEngine
                from presidio_analyzer.nlp_engine import NlpEngineProvider
                from presidio_anonymizer import AnonymizerEngine

                provider = NlpEngineProvider(
                    nlp_configuration={
                        "nlp_engine_name": "spacy",
                        "models": [{"lang_code": "en", "model_name": self._spacy_model}],
                    }
                )
                nlp_engine = provider.create_engine()
                self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
                self._anonymizer = AnonymizerEngine()
            except Exception:
                # Degrade gracefully — Layers 1 & 2 remain fully active.
                self._presidio_failed = True

    def _presidio_scrub(self, text: str) -> str:
        self._ensure_presidio()
        if self._analyzer is None or self._anonymizer is None:
            return text
        try:
            from presidio_anonymizer.entities import OperatorConfig

            results = self._analyzer.analyze(
                text=text, language="en", entities=list(self._presidio_entities)
            )
            if not results:
                return text
            anonymized = self._anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators={"DEFAULT": OperatorConfig("replace", {"new_value": _NER})},
            )
            return anonymized.text
        except Exception:
            return text  # never raise from the logging hot path


class PhiRedactionFilter(logging.Filter):
    """A ``logging.Filter`` that scrubs every record before it is emitted.

    Attach to *handlers* (not loggers) so it guards each sink at the emit
    boundary. See the module docstring for why ``record.args`` must be cleared.
    """

    def __init__(self, redactor: PhiRedactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 (logging API)
        try:
            # Render msg % args ONCE, scrub, then drop args so the handler's
            # subsequent getMessage() cannot re-introduce the originals.
            rendered = record.getMessage()
            record.msg = self._redactor.scrub(rendered)
            record.args = ()

            # Exception & stack text bypass the msg/args path — scrub them too.
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))
                record.exc_text = self._redactor.scrub(exc_text)
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = self._redactor.scrub(record.exc_text)
            if record.stack_info:
                record.stack_info = self._redactor.scrub(record.stack_info)
        except Exception:
            # If sanitization itself fails, suppress content rather than leak.
            record.msg = "[REDACTION-ERROR: log content suppressed]"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True  # keep the (now-clean) record
