"""JSON-RPC interception — the authoritative data path.

Odoo's monetary and tax fields are computed server-side and the OWL DOM renders
them asynchronously (and rounded for display). Rather than scrape rendered text,
we read the server's own JSON-RPC response. All ORM reads ride the single
endpoint ``/web/dataset/call_kw`` (there is **no** bare ``/web/dataset/web_read``
route — ``web_read`` is a model method dispatched through ``call_kw``), so the
predicate matches that path and inspects the request body's ``model``/``method``.

Verified payload shape (Odoo SaaS 19.3, ``account.move/web_read``):

    result -> [ move ]
    move.invoice_line_ids -> [ line, ... ]            # fully-nested line records
    line = { product_id:{id,display_name}, name, quantity, price_unit,
             tax_ids:[{id,display_name}], price_subtotal, price_total,
             currency_id:{id}, display_type:'product' }
    move.tax_totals = { base_amount_currency, tax_amount_currency,
                        total_amount_currency, ... }    # authoritative header totals

Per-line tax is derived as ``price_total - price_subtotal`` (both server-computed).
"""
from __future__ import annotations

from typing import Any, Optional

RPC_PATH = "/web/dataset/call_kw"
MOVE_MODEL = "account.move"
LINE_MODEL = "account.move.line"
READ_METHODS = ("web_read", "read")
SEARCH_METHODS = ("web_search_read", "search_read")

# display_type values that denote a real product line (vs section / note rows)
_PRODUCT_LINE_TYPES = (None, False, "product")


def _params(response) -> dict:
    """Return the JSON-RPC ``params`` from a response's originating request."""
    try:
        body = response.request.post_data_json
    except Exception:
        body = None
    if not isinstance(body, dict):
        return {}
    params = body.get("params")
    return params if isinstance(params, dict) else {}


def is_move_read(response) -> bool:
    """True for the ``account.move`` read that backs the opened invoice form."""
    if RPC_PATH not in response.url:
        return False
    p = _params(response)
    return p.get("model") == MOVE_MODEL and p.get("method") in READ_METHODS


def is_move_search(response) -> bool:
    """True for the list-view search that backs the invoice list."""
    if RPC_PATH not in response.url:
        return False
    p = _params(response)
    return p.get("model") == MOVE_MODEL and p.get("method") in SEARCH_METHODS


# --------------------------------------------------------------------------- #
# Value coercion helpers — tolerant of Odoo's several relational encodings:
#   many2one  -> {"id":.., "display_name":..}  |  [id, "name"]  |  scalar
#   x2many    -> [ {..}, .. ]  |  [ids]
# --------------------------------------------------------------------------- #
def relational_name(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("display_name") or value.get("name")
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[1], str):
        return value[1]
    if isinstance(value, str):
        return value
    return None


def relational_id(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        return value.get("id")
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], int):
        return value[0]
    if isinstance(value, int):
        return value
    return None


def to_number(value: Any) -> Optional[float]:
    if value in (None, False, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> Optional[float]:
    """Coerce to a number rounded to 2 decimals (kills binary-float artifacts)."""
    n = to_number(value)
    return round(n, 2) if n is not None else None


def move_from_result(result: Any) -> Optional[dict]:
    """Normalize a ``web_read``/``read`` result to the single move dict."""
    if isinstance(result, list):
        return result[0] if result else None
    if isinstance(result, dict):
        records = result.get("records")
        if isinstance(records, list):
            return records[0] if records else None
        return result
    return None


def extract_lines(move: dict) -> list[dict]:
    """Extract product lines from a move record (skips section/note rows)."""
    raw = move.get("invoice_line_ids") or move.get("line_ids") or []
    lines: list[dict] = []
    index = 0
    for item in raw:
        if not isinstance(item, dict):
            # Lines returned as bare ids (rare config) — not usable here; the
            # caller falls back to the DOM reader in that case.
            continue
        if item.get("display_type") not in _PRODUCT_LINE_TYPES:
            continue
        taxes = [name for name in (relational_name(t) for t in (item.get("tax_ids") or [])) if name]
        lines.append(
            {
                "line_index": index,
                "product": relational_name(item.get("product_id")) or (item.get("name") or "(unnamed)"),
                "description": item.get("name") or None,
                "quantity": to_number(item.get("quantity")) or 0.0,
                "unit_price": to_number(item.get("price_unit")) or 0.0,
                "taxes": taxes,
                "subtotal": _money(item.get("price_subtotal")) or 0.0,
                "total": _money(item.get("price_total")) or 0.0,
                "currency": relational_name(item.get("currency_id")),
                "_currency_id": relational_id(item.get("currency_id")),
                "source": "json-rpc",
            }
        )
        index += 1
    return lines


def extract_header(move: dict) -> dict:
    """Extract header facts + authoritative totals from ``tax_totals``."""
    totals = move.get("tax_totals") if isinstance(move.get("tax_totals"), dict) else {}

    def pick(*keys) -> Optional[float]:
        for key in keys:
            if totals.get(key) is not None:
                return _money(totals[key])
        return None

    name = move.get("name")
    return {
        "invoice_number": name if name not in (False, "", None) else None,
        "customer": relational_name(move.get("partner_id")) or "(unknown)",
        "invoice_date": str(move["invoice_date"]) if move.get("invoice_date") not in (False, None) else None,
        "state": move.get("state"),
        "currency": relational_name(move.get("currency_id")),
        "_currency_id": relational_id(move.get("currency_id")),
        "amount_untaxed": pick("base_amount_currency", "base_amount", "amount_untaxed"),
        "amount_tax": pick("tax_amount_currency", "tax_amount", "amount_tax"),
        "amount_total": pick("total_amount_currency", "total_amount", "amount_total"),
        "source": "json-rpc:account.move/web_read",
    }


def currency_codes_from_search(result: Any) -> dict[int, str]:
    """Harvest currency id -> code from a list search payload (move web_read only
    carries the currency id, while the list payload carries its display_name)."""
    codes: dict[int, str] = {}
    records = result.get("records") if isinstance(result, dict) else (result if isinstance(result, list) else [])
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        cur = rec.get("currency_id")
        cid, name = relational_id(cur), relational_name(cur)
        if cid is not None and name:
            codes[cid] = name
    return codes
