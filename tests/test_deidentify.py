"""Tests for the HIPAA Safe Harbor de-identification pass."""
from odoo_extractor.deidentify import deidentify
from odoo_extractor.models import (
    ExtractionMetadata,
    ExtractionResult,
    IntegrityChecks,
    InvoiceHeader,
    InvoiceLine,
)


def _result():
    return ExtractionResult(
        metadata=ExtractionMetadata(tool_version="1.0.0", extracted_at="2026-05-29T00:00:00Z",
                                    source="json-rpc"),
        invoice=InvoiceHeader(invoice_number="INV/2026/00001", customer="Deco Addict",
                              invoice_date="2026-05-20", state="posted", amount_total=812.82),
        lines=[InvoiceLine(line_index=0, product="Office Chair",
                           description="Chair for Jane Doe, jane@x.com", quantity=1.0,
                           unit_price=100.0, subtotal=100.0, total=119.0)],
        checks=IntegrityChecks(line_count=1, line_tax_sum=19.0, line_total_sum=119.0),
    )


def test_deidentify_strips_safe_harbor_identifiers():
    out = deidentify(_result(), salt="s3cr3t")
    assert out.invoice.customer.startswith("CUST-")          # (A) names
    assert out.invoice.invoice_number.startswith("INV-")     # (J)/(R)
    assert out.invoice.invoice_date == "2026"                # (C) dates -> year only
    assert out.metadata.deidentified is True
    # product (not an identifier) is retained
    assert out.lines[0].product == "Office Chair"
    # free-text description is swept for residual identifiers
    assert "jane@x.com" not in out.lines[0].description


def test_deidentify_is_deterministic_and_nondestructive():
    src = _result()
    a = deidentify(src, salt="k")
    b = deidentify(src, salt="k")
    assert a.invoice.customer == b.invoice.customer          # stable surrogate
    assert src.invoice.customer == "Deco Addict"             # original untouched


def test_different_salt_changes_surrogate():
    src = _result()
    assert deidentify(src, salt="k1").invoice.customer != deidentify(src, salt="k2").invoice.customer
