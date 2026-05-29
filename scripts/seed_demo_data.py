#!/usr/bin/env python3
"""Seed an Odoo instance with a task-faithful demo dataset (Part 1 setup helper).

The challenge's Part 1 ("Load demo data ... fake customer profiles and invoices")
populates an instance with data to extract. On a fresh odoo.com trial the classic
demo set (and the "Deco Addict" partner) may be absent, so this helper deterministically
creates an equivalent, controlled dataset via Odoo's admin XML-RPC API:

  * products (with sales taxes),
  * customers — including **Deco Addict** — plus a couple of others, and
  * a mix of POSTED and DRAFT customer invoices with multi-line, multi-tax content.

This makes every workflow step meaningful (clear filters -> Posted -> find Deco
Addict among several -> drill in). It is idempotent: re-running will not duplicate.

Credentials are read from the environment (or a local .env):

    ODOO_BASE_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD

Usage:
    python scripts/seed_demo_data.py
"""
from __future__ import annotations

import os
import ssl
import sys
import xmlrpc.client
from collections import Counter

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - certifi is a dependency, but degrade safely
    _SSL = ssl.create_default_context()

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE = os.environ.get("ODOO_BASE_URL", "").rstrip("/")
USER = os.environ.get("ODOO_USERNAME", "")
PWD = os.environ.get("ODOO_PASSWORD", "")
DB = os.environ.get("ODOO_DB") or (BASE.split("//", 1)[-1].split(".", 1)[0] if BASE else "")

if not (BASE and USER and PWD and DB):
    sys.exit("Set ODOO_BASE_URL, ODOO_USERNAME, ODOO_PASSWORD (and ODOO_DB) in the environment or .env.")

common = xmlrpc.client.ServerProxy(f"{BASE}/xmlrpc/2/common", context=_SSL)
uid = common.authenticate(DB, USER, PWD, {})
if not uid:
    sys.exit("Authentication failed (check credentials / DB name).")
models = xmlrpc.client.ServerProxy(f"{BASE}/xmlrpc/2/object", context=_SSL)
print(f"Connected to {BASE} (db={DB}) as uid={uid}; server={common.version().get('server_version')}")


def call(model, method, *args, **kw):
    return models.execute_kw(DB, uid, PWD, model, method, list(args), kw)


def get_or_create(model, domain, vals):
    found = call(model, "search", domain, limit=1)
    return found[0] if found else call(model, "create", vals)


def tax_by_amount(amount):
    ids = call("account.tax", "search", [["type_tax_use", "=", "sale"], ["amount", "=", amount]], limit=1)
    return ids[0] if ids else None


def main():
    tax_std = tax_by_amount(19.0) or tax_by_amount(20.0)   # standard rate
    tax_red = tax_by_amount(7.0) or tax_by_amount(5.0)     # reduced rate
    income = (call("account.account", "search", [["account_type", "=", "income"]], limit=1) or [None])[0]
    journal = (call("account.journal", "search", [["type", "=", "sale"]], limit=1) or [None])[0]
    print(f"taxes: std={tax_std} reduced={tax_red} | income_account={income} | sale_journal={journal}")

    def product(name, price, tax):
        vals = {"name": name, "list_price": price, "type": "consu", "sale_ok": True}
        if tax:
            vals["taxes_id"] = [(6, 0, [tax])]
        return get_or_create("product.product", [["name", "=", name]], vals)

    def partner(name):
        return get_or_create("res.partner", [["name", "=", name]],
                             {"name": name, "is_company": True, "customer_rank": 1})

    products = {
        "chair": product("Office Chair", 120.50, tax_std),
        "desk": product("Standing Desk", 380.00, tax_std),
        "lamp": product("Desk Lamp", 45.00, tax_std),
        "notebook": product("A5 Notebook", 6.90, tax_red),
    }
    partners = {"deco": partner("Deco Addict"), "azure": partner("Azure Interior"),
                "gemini": partner("Gemini Furniture")}

    def line(pid, name, qty, price, tax):
        vals = {"product_id": pid, "name": name, "quantity": qty, "price_unit": price,
                "tax_ids": [(6, 0, [tax] if tax else [])]}
        if income:
            vals["account_id"] = income
        return (0, 0, vals)

    def invoice(partner_id, date, lines, post):
        vals = {"move_type": "out_invoice", "partner_id": partner_id,
                "invoice_date": date, "invoice_line_ids": lines}
        if journal:
            vals["journal_id"] = journal
        mid = call("account.move", "create", vals)
        if post:
            call("account.move", "action_post", [mid])
        return mid

    if call("account.move", "search", [["move_type", "=", "out_invoice"],
                                        ["partner_id", "=", partners["deco"]]]):
        print("Deco Addict already has invoices -> skipping creation (idempotent).")
    else:
        invoice(partners["deco"], "2026-05-20", [
            line(products["chair"], "Office Chair, ergonomic mesh", 2, 120.50, tax_std),
            line(products["desk"], "Standing Desk, electric height-adjustable", 1, 380.00, tax_std),
            line(products["notebook"], "A5 Notebook, dotted (pack)", 10, 6.90, tax_red),
        ], post=True)
        invoice(partners["deco"], "2026-05-25",
                [line(products["lamp"], "Desk Lamp, LED", 3, 45.00, tax_std)], post=False)
        invoice(partners["azure"], "2026-05-18",
                [line(products["lamp"], "Desk Lamp, LED", 5, 45.00, tax_std)], post=True)
        invoice(partners["gemini"], "2026-05-22",
                [line(products["chair"], "Office Chair, ergonomic mesh", 1, 120.50, tax_std)], post=False)
        print("Created Deco Addict (posted+draft), Azure Interior (posted), Gemini Furniture (draft).")

    inv = call("account.move", "search_read", [["move_type", "=", "out_invoice"]],
               fields=["name", "state", "partner_id", "amount_total"])
    print("Customer invoices now:", dict(Counter(i["state"] for i in inv)))
    for i in inv:
        print(f"  {i['state']:7} {str(i['partner_id'][1]):20} {i['name'] or '(draft)':18} total={i['amount_total']}")


if __name__ == "__main__":
    main()
