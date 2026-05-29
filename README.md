# The Enterprise ERP Extraction

A production-grade **Python + Playwright** automation that logs into an Odoo ERP
instance, navigates its reactive (OWL) Invoicing UI, filters to **posted**
customer invoices, drills into a target customer's invoice, and extracts the
billing lines into a clean, standardized JSON document.

Billing data is treated as **Protected Health Information (PHI)**: it is handled
only in memory, **never written to logs**, and the *only* file artifact produced
is the final JSON extract.

> Built for the *Senior AI Automation Engineer Challenge — "The Enterprise ERP
> Extraction"*. Verified end-to-end against a live odoo.com trial (Odoo SaaS 19.3).

---

## Highlights

| Requirement | How it is met |
|---|---|
| **No hardcoded sleeps** | Every wait is signal-based: JSON-RPC `page.expect_response` + Playwright web-first auto-retrying assertions. There is **zero** `time.sleep` / `wait_for_timeout` in the automation. |
| **Resilient locators** | Semantic `[name='<field>']` attributes, ARIA roles, and stable framework classes — never generated OWL ids/hashes or `:nth-child`. |
| **Network interception** | The authoritative data comes from Odoo's `account.move/web_read` JSON-RPC payload (server-computed values), with a resilient DOM read as a graceful fallback. |
| **Tax handling** | Per-line `tax_amount` is derived as `price_total − price_subtotal` and cross-checked against the invoice header totals. |
| **PHI never logged** | Defense-in-depth, deny-list-first in-memory sanitizer (exact-token deny-list + fast regexes + optional Presidio NER), attached at the logging **handler** boundary. |
| **HIPAA know-how** | Safe Harbor de-identification (`--deidentify`), a CFR-mapped safeguards matrix, integrity controls, and data minimization. See the specification PDF. |

---

## Project layout

```
odoo-erp-extraction/
├── README.md                  • this file
├── RUNBOOK.md                 • step-by-step demo script for the call
├── pyproject.toml             • packaging + console entry point (odoo-extract)
├── requirements.txt           • pinned dependencies
├── Makefile                   • setup / seed / extract / demo / test / spec
├── .env.example               • configuration template (copy to .env)
├── src/odoo_extractor/        • the package
│   ├── cli.py                 • CLI entry point & exit codes
│   ├── config.py              • pydantic-settings configuration
│   ├── extractor.py           • workflow orchestration
│   ├── odoo_client.py         • Playwright session, navigation, filters, drill-down
│   ├── interception.py        • JSON-RPC predicates + payload parsing (tax derivation)
│   ├── locators.py            • centralized resilient locators
│   ├── sanitization.py        • in-memory PHI redaction engine + logging filter
│   ├── logging_setup.py       • handler-level sanitized logging
│   ├── deidentify.py          • HIPAA Safe Harbor de-identification pass
│   ├── integrity.py           • SHA-256 fingerprint + header/line reconciliation
│   └── models.py              • typed output schema (Pydantic)
├── scripts/seed_demo_data.py  • Part 1 helper: seed a task-faithful dataset
├── tests/                     • offline unit tests (24 cases)
├── spec/                      • premium specification (HTML/CSS → WeasyPrint PDF)
├── examples/                  • sample outputs (standard + de-identified)
└── output/                    • the JSON extract is written here
```

---

## Quick start

```bash
# 1. One-time setup (venv + deps + Chromium + spaCy model)
make setup

# 2. Configure the target instance
cp .env.example .env        # then edit ODOO_BASE_URL / ODOO_USERNAME / ODOO_PASSWORD

# 3. (Optional) seed a task-faithful dataset on the instance (Part 1)
make seed

# 4. Run the extraction (headless)
make extract                # -> output/invoice_extract.json

# Watch it run for a live demo (headed, with a gentle slow-mo)
make demo

# Emit a HIPAA Safe-Harbor de-identified extract
make deidentify

# Run the test suite
make test

# Build the premium specification PDF (also copied to the Desktop)
make spec
```

Without `make`, the tool is a standard console script / module:

```bash
odoo-extract --customer "Deco Addict" --headed --slow-mo 600
python -m odoo_extractor --help
```

---

## Configuration

Settings resolve in precedence order **CLI flags → environment (`ODOO_*`) / `.env` → defaults**.

| Variable / flag | Default | Purpose |
|---|---|---|
| `ODOO_BASE_URL` / `--base-url` | `http://localhost:8069` | Instance root URL. |
| `ODOO_USERNAME` / `--username` | `admin` | Login / email. |
| `ODOO_PASSWORD` / `--password` | — | Password (prefer the env var; never logged). |
| `ODOO_TARGET_CUSTOMER` / `--customer` | `Deco Addict` | Primary customer to locate. |
| `ODOO_CUSTOMER_ALIASES` | `["Acme Corporation"]` | Fallback names (Odoo 16+ relabelled `res_partner_2`). |
| `ODOO_HEADLESS` / `--headed` | `true` | Run headless, or watch the browser. |
| `--slow-mo` | `0` | Cosmetic per-action delay for demos (not an in-flow wait). |
| `ODOO_DEIDENTIFY` / `--deidentify` | `false` | Emit a Safe Harbor de-identified extract. |
| `ODOO_ENABLE_PRESIDIO` / `--enable-presidio` | `false` | Enable the Presidio NER backstop layer. |
| `ODOO_OUTPUT_PATH` / `--output` | `output/invoice_extract.json` | JSON artifact path. |

**Exit codes:** `0` success · `1` unexpected error · `2` extraction error · `3` customer not found.

---

## How it works (in one breath)

1. **Authenticate** over a persistent browser context (`launch_persistent_context`).
2. **Navigate** to *Customer Invoices* via an action deep-link (auto-detects the modern `/odoo/` vs legacy `/web#` URL scheme).
3. **Clear** any default search facets, then **apply the "Posted" filter** (awaiting the resulting `search_read`).
4. **Find** the target customer's row by its `invoice_partner_display_name` cell and **open** it — wrapping the click in `page.expect_response(account.move/web_read)` so the server payload is captured deterministically (no sleep).
5. **Extract** the lines from that payload (`product`, `quantity`, `unit_price`, `taxes`, `subtotal`, `total`), derive `tax_amount = total − subtotal`, and reconcile against the header `tax_totals`.
6. **Serialize** to JSON. Throughout, the logging layer scrubs any PHI from every sink.

See [`spec/`](spec/) (the PDF) for the full architecture, the resilient-locator
strategy, the network-interception design, and the HIPAA compliance mapping.

---

## Privacy & HIPAA (summary)

The script is a *tool*; HIPAA binds the **covered entity / business associate**
that operates it (45 CFR 160.103). Within that scope this implementation embodies
the relevant **technical safeguards** (45 CFR 164.312) and **minimum-necessary**
principle (164.502(b)):

- **PHI is never logged.** A deny-list-first `logging.Filter` removes the exact
  customer/product/invoice tokens captured at runtime, plus structured PII, before
  any record is emitted — attached at the handler boundary so every sink is guarded.
- **Authoritative, minimal extraction.** Only the needed billing fields are read.
- **Integrity controls (164.312(c)).** A SHA-256 fingerprint over the line set and a
  header/line reconciliation accompany every extract.
- **Optional Safe Harbor de-identification (164.514(b)(2)).** `--deidentify` strips
  the relevant identifiers (names → surrogate, dates → year, account/invoice numbers
  → surrogate) and sweeps free-text — after which the data is no longer PHI.

The full CFR-mapped safeguards matrix and the 18 Safe Harbor identifiers are in the
specification document.
```
