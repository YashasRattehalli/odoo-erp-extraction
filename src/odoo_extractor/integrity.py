"""Integrity controls for the extracted dataset.

Maps to HIPAA 45 CFR 164.312(c)(1)–(c)(2) (integrity / mechanism to authenticate
ePHI) and 164.312(e)(2)(i) (transmission integrity controls):

* a SHA-256 fingerprint over the canonical line set lets a downstream consumer
  detect any alteration of the extract; and
* an independent cross-check reconciles the per-line figures (server-computed
  ``price_subtotal``/``price_total``) against the invoice header totals (the
  server's ``tax_totals`` widget) — two distinct server computations agreeing
  is strong evidence the extraction is faithful.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional, Sequence

from .models import IntegrityChecks, InvoiceHeader, InvoiceLine

_TOLERANCE = 0.01  # currency rounding tolerance (one cent)


def _approx_equal(a: Optional[float], b: Optional[float], tol: float = _TOLERANCE) -> Optional[bool]:
    if a is None or b is None:
        return None
    return abs(round(a - b, 4)) <= tol


def lines_fingerprint(lines: Sequence[InvoiceLine]) -> str:
    """Deterministic SHA-256 over the canonical JSON of the line set."""
    payload = [line.model_dump() for line in lines]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_checks(lines: Sequence[InvoiceLine], header: InvoiceHeader) -> IntegrityChecks:
    """Reconcile line-level figures against the header totals."""
    tax_sum = round(sum(line.tax_amount for line in lines), 2)
    total_sum = round(sum(line.total for line in lines), 2)
    return IntegrityChecks(
        line_count=len(lines),
        line_tax_sum=tax_sum,
        line_total_sum=total_sum,
        line_tax_sum_equals_header=_approx_equal(tax_sum, header.amount_tax),
        line_total_sum_equals_amount_total=_approx_equal(total_sum, header.amount_total),
    )
