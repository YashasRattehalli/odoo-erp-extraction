"""Optional HIPAA Safe Harbor de-identification of the output (45 CFR 164.514(b)(2)).

When ``--deidentify`` is requested, the extract is transformed so it no longer
contains the Safe Harbor identifiers, after which it is — by regulation — no
longer PHI (45 CFR 164.514(a)). For invoice/billing data the relevant
identifiers are:

* (A) Names ............... customer name -> salted-HMAC surrogate
* (C) Dates .............. invoice date -> year only
* (J)/(R) Account/other .. invoice number -> salted-HMAC surrogate

Monetary figures, quantities and product names are not among the 18 identifiers
and are retained (the analytic value of the extract). Free-text descriptions are
additionally swept for residual identifiers via the same redactor used for logs,
because notes are a common leak path (164.514(b)(2)(ii) "no actual knowledge").

Surrogates are deterministic per (value, salt) so the same entity maps to the
same key across runs — enabling joins without re-identification (164.514(c)).
"""
from __future__ import annotations

import hashlib
import hmac

from .models import ExtractionResult
from .sanitization import PhiRedactor


def _surrogate(value: object, salt: str, prefix: str) -> str:
    digest = hmac.new(salt.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _year_only(date_str: object) -> str | None:
    if not date_str:
        return None
    text = str(date_str)
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else None


def deidentify(result: ExtractionResult, *, salt: str, redactor: PhiRedactor | None = None) -> ExtractionResult:
    """Return a Safe-Harbor de-identified copy of ``result`` (input unchanged)."""
    data = result.model_copy(deep=True)
    header = data.invoice

    header.customer = _surrogate(header.customer, salt, "CUST")            # (A) names
    if header.invoice_number:
        header.invoice_number = _surrogate(header.invoice_number, salt, "INV")  # (J)/(R)
    header.invoice_date = _year_only(header.invoice_date)                  # (C) dates

    # (R) sweep free-text line descriptions for any residual identifiers.
    scrub = (redactor or PhiRedactor()).scrub
    for line in data.lines:
        if line.description:
            line.description = scrub(line.description)

    data.metadata.deidentified = True
    return data
