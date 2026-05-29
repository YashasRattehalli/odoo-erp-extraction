# Demo Runbook — The Enterprise ERP Extraction

A tight, ~6-minute script for the evaluation call. Everything below has been
verified end-to-end against the live odoo.com trial (Odoo SaaS 19.3).

---

## 0 · Before the call (one time)

```bash
cd odoo-erp-extraction
make setup            # venv + deps + Chromium + spaCy model  (~3–4 min)
cp .env.example .env  # already provided locally; confirm ODOO_BASE_URL / creds
make seed             # ensures Deco Addict + posted/draft invoices exist (idempotent)
make test             # green: 24 passed
```

Trial used for the demo: `https://yr-solutions-srl.odoo.com` · customer **Deco Addict**.

---

## 1 · Talk track (≈30s)

> "This logs into a live Odoo ERP, filters to *posted* invoices, finds the
> **Deco Addict** invoice, and extracts the billing lines to clean JSON. Two
> things to watch: it uses **network interception** for authoritative values and
> **never sleeps**; and it treats billing data as **PHI** — nothing sensitive
> ever reaches the logs."

---

## 2 · Live run — watch the browser (≈90s)

```bash
make demo      # headed, gentle slow-mo so each step is visible
```

Narrate as it goes: **login → Customer Invoices → clear filters → apply *Posted*
→ locate Deco Addict → open → Invoice Lines → done.** Point out the console:
only step messages, counts, and an integrity check — **no customer or product
names**.

---

## 3 · Show the result (≈60s)

```bash
cat output/invoice_extract.json
```

Call out: `tax_amount` per line is **derived** (`total − subtotal`), the header
totals **reconcile** (`checks.*_equals_* = true`), and there's a SHA-256
integrity fingerprint. Source is `json-rpc:account.move/web_read` — i.e. the
server's own numbers, not scraped text.

---

## 4 · Prove the privacy guardrail (≈45s)

```bash
# The customer/product names exist ONLY in the JSON file — never in any log line:
make extract 2>&1 | grep -i "Deco Addict\|Office Chair" || echo "✓ no PHI in logs"
grep -i "Deco Addict" output/invoice_extract.json | head -1   # present in the OUTPUT only
```

Then the HIPAA flourish:

```bash
make deidentify
cat output/invoice_extract.deidentified.json    # customer→CUST-…, date→year, invoice#→INV-…
```

---

## 5 · Hand over (≈30s)

- **Specification PDF** — on the Desktop: *The Enterprise ERP Extraction — Technical Specification.pdf*
  (architecture, resilient-locator strategy, network-interception design, full HIPAA mapping).
- **Code** — this repository; `src/odoo_extractor/` is the package, `tests/` the suite.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `customer not found` (exit 3) | Run `make seed`; confirm `ODOO_TARGET_CUSTOMER`. |
| Login loops | Delete `.pw-profile/` and re-run; re-check `.env` credentials. |
| Trial expired / different instance | Point `.env` at the new instance and `make seed`. |
| Want a clean slate | `make clean` (removes profile + generated outputs). |
