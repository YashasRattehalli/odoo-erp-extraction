"""Tests for the output models and integrity controls."""
from odoo_extractor.integrity import build_checks, lines_fingerprint
from odoo_extractor.models import InvoiceHeader, InvoiceLine, round_money


def _line(idx, sub, tot, **kw):
    base = dict(line_index=idx, product=f"P{idx}", quantity=1.0, unit_price=sub,
                subtotal=sub, total=tot)
    base.update(kw)
    return InvoiceLine(**base)


def test_round_money_kills_float_drift():
    assert round_money(0.1 + 0.2) == 0.3
    assert round_money(122.82000000000001) == 122.82
    assert round_money(None) is None


def test_tax_amount_is_total_minus_subtotal():
    line = _line(0, 241.0, 286.79)
    assert line.tax_amount == 45.79
    assert "tax_amount" in line.model_dump()  # computed field is serialized


def test_build_checks_reconciles_with_header():
    lines = [_line(0, 241.0, 286.79), _line(1, 380.0, 452.2), _line(2, 69.0, 73.83)]
    header = InvoiceHeader(customer="X", amount_tax=122.82, amount_total=812.82)
    checks = build_checks(lines, header)
    assert checks.line_count == 3
    assert checks.line_tax_sum == 122.82
    assert checks.line_total_sum == 812.82
    assert checks.line_tax_sum_equals_header is True
    assert checks.line_total_sum_equals_amount_total is True


def test_build_checks_flags_mismatch():
    lines = [_line(0, 100.0, 119.0)]
    header = InvoiceHeader(customer="X", amount_tax=999.0, amount_total=999.0)
    checks = build_checks(lines, header)
    assert checks.line_tax_sum_equals_header is False


def test_build_checks_none_when_header_missing():
    lines = [_line(0, 100.0, 119.0)]
    checks = build_checks(lines, InvoiceHeader(customer="X"))
    assert checks.line_tax_sum_equals_header is None


def test_fingerprint_is_deterministic_and_sensitive():
    a = [_line(0, 241.0, 286.79)]
    b = [_line(0, 241.0, 286.79)]
    c = [_line(0, 241.0, 290.00)]
    assert lines_fingerprint(a) == lines_fingerprint(b)
    assert lines_fingerprint(a) != lines_fingerprint(c)
    assert len(lines_fingerprint(a)) == 64  # sha256 hex
