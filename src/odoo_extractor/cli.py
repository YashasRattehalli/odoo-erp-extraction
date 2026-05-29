"""Command-line entry point.

Configuration precedence: CLI flags > environment (ODOO_*) / .env > defaults.
Only the final JSON extract is written to disk; everything emitted to the
console passes through the PHI redaction filter.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pydantic import SecretStr

from . import __version__
from .config import Settings
from .extractor import run_extraction
from .logging_setup import configure_logging
from .odoo_client import CustomerNotFoundError, ExtractionError
from .sanitization import PhiRedactor

logger = logging.getLogger("odoo_extractor.cli")

# Process exit codes (stable contract for CI / orchestration).
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_EXTRACTION_ERROR = 2
EXIT_CUSTOMER_NOT_FOUND = 3


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="odoo-extract",
        description="Resilient, HIPAA-aware extraction of Odoo invoice lines to JSON.",
    )
    p.add_argument("--base-url", help="Odoo instance root URL.")
    p.add_argument("--db", help="Database name (optional for single-DB SaaS trials).")
    p.add_argument("--username", help="Login / email.")
    p.add_argument("--password", help="Account password (prefer ODOO_PASSWORD env).")
    p.add_argument("--customer", help="Primary target customer name.")
    p.add_argument("--alias", action="append", dest="aliases", metavar="NAME",
                   help="Fallback customer name (repeatable).")
    p.add_argument("--output", help="Path for the JSON extract.")
    headless = p.add_mutually_exclusive_group()
    headless.add_argument("--headed", dest="headless", action="store_false", default=None,
                          help="Show the browser window (useful for live demos).")
    headless.add_argument("--headless", dest="headless", action="store_true", default=None,
                          help="Run the browser headless (default).")
    p.add_argument("--slow-mo", type=int, dest="slow_mo_ms",
                   help="Cosmetic per-action delay in ms (demos only; not an in-flow wait).")
    p.add_argument("--deidentify", action="store_true", default=None,
                   help="Apply a HIPAA Safe Harbor de-identification pass to the output.")
    p.add_argument("--enable-presidio", action="store_true", default=None,
                   help="Enable the Presidio NER backstop in the log sanitizer.")
    p.add_argument("--user-data-dir", help="Persistent browser profile directory.")
    p.add_argument("--log-level", help="Log level (DEBUG/INFO/WARNING/ERROR).")
    p.add_argument("--log-file", help="Optional sanitized log file path.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _settings_from_args(args: argparse.Namespace) -> Settings:
    base = Settings()  # env / .env / defaults
    overrides: dict = {}
    mapping = {
        "base_url": args.base_url,
        "db": args.db,
        "username": args.username,
        "target_customer": args.customer,
        "customer_aliases": args.aliases,
        "output_path": Path(args.output) if args.output else None,
        "headless": args.headless,
        "slow_mo_ms": args.slow_mo_ms,
        "deidentify": args.deidentify,
        "enable_presidio": args.enable_presidio,
        "user_data_dir": Path(args.user_data_dir) if args.user_data_dir else None,
        "log_level": args.log_level,
        "log_file": Path(args.log_file) if args.log_file else None,
    }
    for key, value in mapping.items():
        if value is not None:
            overrides[key] = value
    if args.password is not None:
        overrides["password"] = SecretStr(args.password)
    return base.model_copy(update=overrides)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = _settings_from_args(args)

    redactor = PhiRedactor(enable_presidio=settings.enable_presidio)
    configure_logging(settings.log_level, redactor=redactor, log_file=settings.log_file)
    # The instance endpoint is infrastructure, not PHI — keep it readable in logs.
    redactor.add_allow(settings.base_url)
    logger.info("odoo-extract v%s starting | %s", __version__, json.dumps(settings.safe_summary()))

    try:
        result = run_extraction(settings, redactor)
    except CustomerNotFoundError:
        logger.error("Target customer (and aliases) matched no posted invoice.")
        return EXIT_CUSTOMER_NOT_FOUND
    except ExtractionError as err:
        logger.error("Extraction failed: %s", err)
        return EXIT_EXTRACTION_ERROR
    except Exception:  # noqa: BLE001 — top-level guard; traceback is PHI-scrubbed
        logger.exception("Unexpected error during extraction.")
        return EXIT_UNEXPECTED

    out_path = Path(settings.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.to_json(), encoding="utf-8")

    logger.info(
        "Wrote %d line(s) -> %s | integrity tax_match=%s total_match=%s | sha256=%s%s",
        len(result.lines), out_path,
        result.checks.line_tax_sum_equals_header,
        result.checks.line_total_sum_equals_amount_total,
        (result.metadata.integrity_sha256 or "")[:12],
        " | DE-IDENTIFIED" if result.metadata.deidentified else "",
    )
    return EXIT_OK
