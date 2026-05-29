"""Tests for JSON-RPC payload parsing and tax derivation."""
from odoo_extractor.interception import (
    currency_codes_from_search,
    extract_header,
    extract_lines,
    move_from_result,
    relational_id,
    relational_name,
)
from odoo_extractor.models import InvoiceLine

# A move payload mirroring the verified saas-19.3 web_read shape, with both the
# dict and list relational encodings, plus a section row that must be skipped.
MOVE = {
    "name": "INV/2026/00001",
    "partner_id": {"id": 9, "display_name": "Deco Addict"},
    "invoice_date": "2026-05-20",
    "state": "posted",
    "currency_id": {"id": 126},
    "tax_totals": {
        "base_amount_currency": 690.0,
        "tax_amount_currency": 122.82000000000001,  # float artifact -> must round
        "total_amount_currency": 812.82,
    },
    "invoice_line_ids": [
        {
            "display_type": "product",
            "product_id": {"id": 1, "display_name": "Office Chair"},
            "name": "Office Chair, ergonomic mesh",
            "quantity": 2.0, "price_unit": 120.5,
            "tax_ids": [{"id": 18, "display_name": "19%"}],
            "price_subtotal": 241.0, "price_total": 286.79,
            "currency_id": {"id": 126},
        },
        {"display_type": "line_section", "name": "Furniture"},  # skipped
        {
            "display_type": "product",
            "product_id": [2, "Standing Desk"],                  # list encoding
            "name": "Standing Desk",
            "quantity": 1.0, "price_unit": 380.0,
            "tax_ids": [[18, "19%"]],                            # list encoding
            "price_subtotal": 380.0, "price_total": 452.2,
            "currency_id": [126, "EUR"],                         # carries code
        },
    ],
}


def test_relational_coercion():
    assert relational_name({"id": 1, "display_name": "X"}) == "X"
    assert relational_name([2, "Y"]) == "Y"
    assert relational_id({"id": 7}) == 7
    assert relational_id([7, "Z"]) == 7


def test_move_from_result_variants():
    assert move_from_result([MOVE]) is MOVE
    assert move_from_result({"records": [MOVE]}) is MOVE
    assert move_from_result(MOVE) is MOVE
    assert move_from_result([]) is None


def test_extract_lines_skips_sections_and_derives_tax():
    lines = extract_lines(MOVE)
    assert len(lines) == 2  # the section row is excluded
    chair = InvoiceLine(**{k: v for k, v in lines[0].items() if not k.startswith("_")})
    assert chair.product == "Office Chair"
    assert chair.tax_amount == 45.79          # 286.79 - 241.00
    desk = lines[1]
    assert desk["product"] == "Standing Desk"  # parsed from list encoding
    assert desk["currency"] == "EUR"           # code from list encoding


def test_extract_header_uses_tax_totals_and_rounds():
    h = extract_header(MOVE)
    assert h["invoice_number"] == "INV/2026/00001"
    assert h["customer"] == "Deco Addict"
    assert h["amount_untaxed"] == 690.0
    assert h["amount_tax"] == 122.82           # rounded, no float artifact
    assert h["amount_total"] == 812.82
    assert h["_currency_id"] == 126


def test_draft_move_has_no_invoice_number():
    draft = dict(MOVE, name=False)
    assert extract_header(draft)["invoice_number"] is None


def test_currency_codes_from_search():
    result = {"records": [{"currency_id": {"id": 126, "display_name": "EUR"}}]}
    assert currency_codes_from_search(result) == {126: "EUR"}
