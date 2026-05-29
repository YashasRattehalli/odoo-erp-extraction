"""Workflow orchestration: authenticate -> navigate -> filter -> drill -> extract.

The end-to-end sequence mirrors the challenge brief. All values land in the
typed :class:`ExtractionResult`; the authoritative source is the JSON-RPC
payload, with a resilient DOM read as a graceful fallback. PHI tokens discovered
during the run (customer, products, invoice number) are registered with the
redactor as defense-in-depth — though by construction we never log raw values.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import __version__
from .config import Settings
from .integrity import build_checks, lines_fingerprint
from .interception import extract_header, extract_lines
from .deidentify import deidentify
from .models import ExtractionMetadata, ExtractionResult, InvoiceHeader, InvoiceLine
from .odoo_client import OdooSession
from .sanitization import PhiRedactor

logger = logging.getLogger("odoo_extractor.extractor")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_extraction(settings: Settings, redactor: PhiRedactor) -> ExtractionResult:
    """Execute the full extraction workflow and return the standardized result."""
    candidate_names = [settings.target_customer, *settings.customer_aliases]
    # Defense-in-depth: register target names BEFORE any navigation so even an
    # unexpected log line cannot leak them.
    redactor.register_secrets(candidate_names)

    with OdooSession(settings, redactor) as session:
        session.login()
        session.goto_customer_invoices()
        session.clear_search_facets()          # "clear default filters"
        session.apply_posted_filter()          # "apply the 'Posted' status filter"
        row, _matched = session.find_customer_row(candidate_names)
        move = session.open_invoice(row)       # authoritative web_read captured here

        state = session.current_form_state()
        if state and state != "posted":
            logger.warning("Opened invoice state is not 'posted' as expected.")

        session.ensure_invoice_lines_tab()

        header_d = extract_header(move)
        line_ds = extract_lines(move)
        used_source = "json-rpc"
        if not line_ds:
            logger.warning("RPC payload had no nested line records; using DOM fallback.")
            line_ds = session.read_lines_from_dom()
            used_source = "dom-fallback"

        # Resolve currency codes (the move read carries only the currency id).
        header_currency = session.resolve_currency(header_d.get("currency"), header_d.pop("_currency_id", None))
        header_d["currency"] = header_currency
        for ld in line_ds:
            ld["currency"] = session.resolve_currency(ld.get("currency"), ld.pop("_currency_id", None)) or header_currency

        # Register discovered PHI tokens (still never logged in the clear).
        redactor.register_secret(header_d.get("customer"))
        redactor.register_secret(header_d.get("invoice_number"))
        redactor.register_secrets(ld.get("product") for ld in line_ds)
        redactor.register_secrets(ld.get("description") for ld in line_ds)

        header = InvoiceHeader(**{k: v for k, v in header_d.items() if not k.startswith("_")})
        lines = [InvoiceLine(**{k: v for k, v in ld.items() if not k.startswith("_")}) for ld in line_ds]

        if not lines:
            raise RuntimeError("No invoice lines could be extracted from RPC or DOM.")

        checks = build_checks(lines, header)
        fingerprint = lines_fingerprint(lines)
        metadata = ExtractionMetadata(
            tool_version=__version__,
            extracted_at=_utc_now_iso(),
            source=header.source if used_source == "json-rpc" else "dom-fallback",
            instance_url=settings.base_url,
            url_scheme=session.url_scheme,
            deidentified=False,
            integrity_sha256=fingerprint,
        )
        result = ExtractionResult(metadata=metadata, invoice=header, lines=lines, checks=checks)

    logger.info(
        "Extraction complete: %d line(s) | source=%s | integrity tax_match=%s total_match=%s",
        len(result.lines), metadata.source,
        checks.line_tax_sum_equals_header, checks.line_total_sum_equals_amount_total,
    )

    if settings.deidentify:
        logger.info("Applying HIPAA Safe Harbor de-identification pass (45 CFR 164.514(b)(2)).")
        result = deidentify(result, salt=settings.deid_salt.get_secret_value(), redactor=redactor)

    return result
