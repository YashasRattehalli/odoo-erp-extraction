#!/usr/bin/env python3
"""Render the premium specification PDF (HTML/CSS -> WeasyPrint).

Highlights embedded code with Pygments, injects a verified sample extract, and
writes the PDF both into ``spec/`` and onto the Desktop. Fonts are bundled local
TTFs (Playfair Display / Inter / JetBrains Mono) and embedded into the PDF.

    python spec/build_spec.py [--out /path/to/output.pdf]
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import BashLexer, JsonLexer, PythonLexer
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration

SPEC_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SPEC_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
try:
    from odoo_extractor import __version__ as VERSION
except Exception:
    VERSION = "1.0.0"

PYGMENTS_STYLE = "friendly"

# --------------------------------------------------------------------------- #
# Embedded code snippets (kept faithful to the implementation).
# --------------------------------------------------------------------------- #
CODE_PREDICATE = '''RPC_PATH = "/web/dataset/call_kw"   # web_read is dispatched through call_kw

def is_move_read(response) -> bool:
    if RPC_PATH not in response.url:
        return False
    params = (response.request.post_data_json or {}).get("params", {})
    return (params.get("model") == "account.move"
            and params.get("method") in ("web_read", "read"))

# Opening the invoice IS the trigger; we await its response (no sleep) and read
# the server's authoritative values straight from the JSON-RPC payload:
with page.expect_response(is_move_read) as info:
    row.locator("td.o_data_cell").first.click()
move = info.value.json()["result"][0]
# Per line:  tax_amount = price_total - price_subtotal   (both server-computed)'''

CODE_FILTER = '''class PhiRedactionFilter(logging.Filter):
    """Scrub every record before it reaches a sink; attach to HANDLERS."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Render msg % args ONCE, scrub, then DROP args so the handler's later
        # getMessage() cannot re-introduce the original PHI values.
        record.msg = self._redactor.scrub(record.getMessage())
        record.args = ()
        if record.exc_info:                       # tracebacks bypass msg % args
            text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = self._redactor.scrub(text)
            record.exc_info = None
        return True                               # keep the now-clean record'''

CODE_CLI = '''# Headless extraction (configuration via ODOO_* env vars / .env)
odoo-extract --customer "Deco Addict"

# Watch it run live during the demo
odoo-extract --headed --slow-mo 600

# Emit a HIPAA Safe Harbor de-identified extract
odoo-extract --deidentify --output output/invoice_extract.deidentified.json'''

RUN_LOG = '''2026-05-29 04:14:41  INFO  odoo_extractor.cli       odoo-extract v1.0.0 starting | {"base_url": "https://yr-solutions-srl.odoo.com", "headless": true, "deidentify": false, ...}
2026-05-29 04:14:42  INFO  odoo_extractor.client    Browser session started (headless=True).
2026-05-29 04:14:44  INFO  odoo_extractor.client    Submitted credentials for the configured service account.
2026-05-29 04:14:45  INFO  odoo_extractor.client    Customer Invoices list loaded.
2026-05-29 04:14:45  INFO  odoo_extractor.client    Cleared 0 pre-existing search facet(s).
2026-05-29 04:14:45  INFO  odoo_extractor.client    Applied the 'Posted' status filter.
2026-05-29 04:14:45  INFO  odoo_extractor.client    Located a matching invoice row for the target customer.
2026-05-29 04:14:46  INFO  odoo_extractor.client    Opened the invoice form (authoritative record captured via JSON-RPC).
2026-05-29 04:14:46  INFO  odoo_extractor.extractor Extraction complete: 3 line(s) | integrity tax_match=True total_match=True
2026-05-29 04:14:46  INFO  odoo_extractor.cli       Wrote 3 line(s) -> output/invoice_extract.json | sha256=dc149831b8a0'''


def hl(code: str, lexer) -> str:
    return highlight(code, lexer, HtmlFormatter(nowrap=False))


def hl_long(code: str, lexer) -> str:
    # Tag long listings so they may break across pages (avoids orphaned heading).
    return highlight(code, lexer, HtmlFormatter(nowrap=False, cssclass="highlight long"))


def plain(text: str) -> str:
    return f'<div class="highlight"><pre>{html.escape(text)}</pre></div>'


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the specification PDF.")
    default_out = Path.home() / "Desktop" / "The Enterprise ERP Extraction - Technical Specification.pdf"
    parser.add_argument("--out", type=Path, default=default_out, help="Output PDF path.")
    args = parser.parse_args()

    sample_path = PROJECT_DIR / "examples" / "sample_output.json"
    sample_json = sample_path.read_text(encoding="utf-8") if sample_path.exists() else "{}"

    env = Environment(loader=FileSystemLoader(str(SPEC_DIR)), autoescape=select_autoescape(enabled_extensions=()))
    template = env.get_template("template.html.j2")
    rendered = template.render(
        version=VERSION,
        date=datetime.now().strftime("%B %-d, %Y"),
        code_predicate=hl(CODE_PREDICATE, PythonLexer()),
        code_filter=hl(CODE_FILTER, PythonLexer()),
        code_cli=hl(CODE_CLI, BashLexer()),
        sample_json=hl_long(sample_json, JsonLexer()),
        run_log=plain(RUN_LOG),
    )

    font_config = FontConfiguration()
    pygments_css = HtmlFormatter(style=PYGMENTS_STYLE).get_style_defs(".highlight")
    stylesheets = [
        CSS(filename=str(SPEC_DIR / "print.css"), font_config=font_config),
        CSS(string=pygments_css, font_config=font_config),
    ]

    document = HTML(string=rendered, base_url=str(SPEC_DIR))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    document.write_pdf(str(args.out), stylesheets=stylesheets, font_config=font_config)

    repo_copy = SPEC_DIR / "specification.pdf"
    document.write_pdf(str(repo_copy), stylesheets=stylesheets, font_config=font_config)

    print(f"Wrote: {args.out}")
    print(f"Wrote: {repo_copy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
