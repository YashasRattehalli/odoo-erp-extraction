"""Odoo ERP Extraction — resilient, HIPAA-aware invoice-line extraction.

A production-grade Playwright automation that authenticates to an Odoo ERP
instance, navigates the reactive (OWL) Invoicing UI, filters to *posted*
customer invoices, drills into a target customer's invoice, and extracts the
billing lines into a standardized JSON document.

Design tenets
-------------
* **Billing data is treated as Protected Health Information (PHI).** It is held
  only in transient memory, is never written to logs, and the *only* file
  artifact produced is the final JSON extract.
* **Authoritative data comes from the network, not the DOM.** Monetary and tax
  values are read from Odoo's JSON-RPC responses (server-computed), with a
  resilient DOM read as a graceful fallback.
* **No hardcoded waits.** Synchronisation is driven by network responses and
  Playwright's auto-retrying, web-first assertions — never ``time.sleep`` or
  ``page.wait_for_timeout``.

See :mod:`odoo_extractor.sanitization` and :mod:`odoo_extractor.logging_setup`
for the in-memory log-sanitization guarantee, and
:mod:`odoo_extractor.deidentify` for the HIPAA Safe Harbor de-identification
pass (45 CFR 164.514(b)(2)).
"""
from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Yashas Rattehalli"
__all__ = ["__version__", "__author__"]
