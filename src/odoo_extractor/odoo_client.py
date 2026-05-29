"""Playwright session and navigation flow for the Odoo web client.

Synchronisation contract: **no hardcoded waits.** Every wait is signal-based —
either a JSON-RPC response (``page.expect_response``) or an auto-retrying
web-first assertion (``expect(locator)...``). The OWL framework replaces DOM
nodes on each reactive update, so locators are re-queried on use and element
handles are never cached.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    expect,
    sync_playwright,
)

from . import locators as L
from .config import Settings
from .interception import (
    currency_codes_from_search,
    is_move_read,
    is_move_search,
    move_from_result,
    to_number,
)
from .sanitization import PhiRedactor

logger = logging.getLogger("odoo_extractor.client")


class ExtractionError(RuntimeError):
    """A recoverable, expected failure in the extraction workflow."""


class CustomerNotFoundError(ExtractionError):
    """The requested customer (and aliases) matched no invoice row."""


class OdooSession:
    """A persistent, authenticated Playwright session against an Odoo instance.

    Use as a context manager so the browser is always torn down::

        with OdooSession(settings, redactor) as session:
            session.login()
            session.goto_customer_invoices()
            ...
    """

    def __init__(self, settings: Settings, redactor: PhiRedactor) -> None:
        self.settings = settings
        self.redactor = redactor
        self._pw = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.url_scheme: Optional[str] = None
        self._currency_codes: dict[int, str] = {}

    # -- lifecycle --------------------------------------------------------- #
    def __enter__(self) -> "OdooSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        s = self.settings
        Path(s.user_data_dir).mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        # A persistent context preserves the session cookie across runs.
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(s.user_data_dir),
            headless=s.headless,
            slow_mo=s.slow_mo_ms,  # cosmetic only (demos); NOT an in-flow wait
            viewport={"width": 1680, "height": 1050},
            accept_downloads=False,
        )
        self._context.set_default_timeout(s.action_timeout_ms)
        self._context.set_default_navigation_timeout(s.nav_timeout_ms)
        expect.set_options(timeout=s.action_timeout_ms)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self.page.on("response", self._on_response)
        logger.info("Browser session started (headless=%s).", s.headless)

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
        logger.info("Browser session closed.")

    # -- passive currency harvesting (id -> code) -------------------------- #
    def _on_response(self, response: Response) -> None:
        if not is_move_search(response):
            return
        try:
            self._currency_codes.update(currency_codes_from_search(response.json().get("result")))
        except Exception:
            pass  # never let telemetry harvesting affect the run

    def resolve_currency(self, code: Optional[str], currency_id: Optional[int]) -> Optional[str]:
        """Prefer an embedded code; otherwise map the id via harvested codes."""
        if code:
            return code
        if currency_id is not None:
            return self._currency_codes.get(currency_id)
        return None

    # -- authentication ---------------------------------------------------- #
    def login(self) -> None:
        s = self.settings
        self.page.goto(f"{s.base_url.rstrip('/')}/web/login", wait_until="domcontentloaded")
        if self.page.locator(L.LOGIN_INPUT).count() > 0:
            self.page.fill(L.LOGIN_INPUT, s.username)
            self.page.fill(L.PASSWORD_INPUT, s.password.get_secret_value())
            self.page.click(L.SUBMIT_BUTTON)
            logger.info("Submitted credentials for the configured service account.")
        else:
            logger.info("Existing authenticated session detected; skipping login form.")
        try:
            expect(self.page.locator(L.APP_SHELL).first).to_be_visible(timeout=s.nav_timeout_ms)
        except (PlaywrightTimeoutError, AssertionError) as err:
            if self.page.locator(L.LOGIN_ERROR).count() > 0:
                raise ExtractionError("Authentication failed: credentials rejected by the instance.") from err
            raise ExtractionError("Authentication did not reach the application shell.") from err
        self.url_scheme = "modern" if "/odoo/" in self.page.url else "legacy"
        logger.info("Authenticated. URL scheme: %s.", self.url_scheme)

    # -- navigation -------------------------------------------------------- #
    def goto_customer_invoices(self) -> None:
        base = self.settings.base_url.rstrip("/")
        candidates = [
            f"{base}/odoo/action-{L.ACTION_XMLID}",   # modern scheme (17.2+/18/19)
            f"{base}/web#action={L.ACTION_XMLID}",     # legacy hash scheme (<=17.x)
        ]
        last_error: Optional[Exception] = None
        for url in candidates:
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                expect(self.page.locator(L.LIST_RENDERER).first).to_be_visible()
                self.url_scheme = "modern" if "/odoo/" in self.page.url else "legacy"
                logger.info("Customer Invoices list loaded.")
                return
            except (PlaywrightTimeoutError, AssertionError) as err:
                last_error = err
                logger.warning("Navigation via one URL scheme failed; trying fallback.")
        raise ExtractionError("Could not load the Customer Invoices list view.") from last_error

    # -- search / filter --------------------------------------------------- #
    def clear_search_facets(self) -> None:
        """Remove any pre-applied search facets (defensive 'clear default filters').

        Uses an auto-retrying count assertion to wait for each removal's
        re-render — no sleeps, no network assumptions."""
        removed = 0
        for _ in range(10):  # bounded guard against an un-removable facet
            count = self.page.locator(L.SEARCH_FACET).count()
            if count == 0:
                break
            self.page.locator(L.FACET_REMOVE).first.click()
            expect(self.page.locator(L.SEARCH_FACET)).to_have_count(count - 1)
            removed += 1
        logger.info("Cleared %d pre-existing search facet(s).", removed)

    def apply_posted_filter(self) -> None:
        """Open the search dropdown and apply the predefined 'Posted' filter."""
        self.page.locator(L.SEARCH_DROPDOWN_TOGGLER).first.click()
        expect(self.page.locator(L.SEARCH_MENU).first).to_be_visible()
        posted = self._posted_filter_item()
        # The click triggers a server search_read; await it instead of sleeping.
        with self.page.expect_response(is_move_search):
            posted.click()
        self.page.keyboard.press("Escape")
        expect(self.page.locator(L.SEARCH_FACET).filter(has_text=L.POSTED_FILTER_LABEL).first).to_be_visible()
        logger.info("Applied the 'Posted' status filter.")

    def _posted_filter_item(self) -> Locator:
        page = self.page
        for getter in (
            lambda: page.get_by_role("menuitemcheckbox", name=L.POSTED_FILTER_LABEL),
            lambda: page.get_by_role("menuitem", name=L.POSTED_FILTER_LABEL),
            lambda: page.locator(L.SEARCH_MENU).get_by_text(L.POSTED_FILTER_LABEL, exact=True),
        ):
            loc = getter()
            if loc.count() > 0:
                return loc.first
        raise ExtractionError("The 'Posted' filter could not be located in the search menu.")

    # -- drill-down -------------------------------------------------------- #
    def find_customer_row(self, names: list[str]) -> tuple[Locator, str]:
        """Return the first invoice row whose customer matches one of ``names``."""
        expect(self.page.locator(L.LIST_RENDERER).first).to_be_visible()
        for name in names:
            row = self.page.locator(L.DATA_ROW).filter(
                has=self.page.locator(L.PARTNER_CELL, has_text=name)
            )
            if row.count() > 0:
                logger.info("Located a matching invoice row for the target customer.")
                return row.first, name
        raise CustomerNotFoundError(
            "No posted invoice row matched the target customer or its configured aliases."
        )

    def open_invoice(self, row: Locator) -> dict:
        """Open the invoice and return the server's ``web_read`` move record.

        The click triggers ``account.move/web_read``; we await that response and
        use its JSON body as the authoritative data source.
        """
        with self.page.expect_response(is_move_read, timeout=self.settings.nav_timeout_ms) as info:
            row.locator(L.DATA_CELL).first.click()
        response = info.value
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            raise ExtractionError("Odoo returned a JSON-RPC error while opening the invoice.")
        move = move_from_result(body.get("result"))
        if not move:
            raise ExtractionError("The invoice read response contained no record.")
        expect(self.page.locator(L.FORM_VIEW).first).to_be_visible()
        logger.info("Opened the invoice form (authoritative record captured via JSON-RPC).")
        return move

    def current_form_state(self) -> Optional[str]:
        """Untranslated status value from the form's status bar (e.g. 'posted')."""
        el = self.page.locator(L.STATUSBAR_CURRENT)
        return el.first.get_attribute("data-value") if el.count() > 0 else None

    def ensure_invoice_lines_tab(self) -> None:
        """Activate the 'Invoice Lines' notebook tab (it is the default, so this
        is usually a no-op) and wait for its line table to be present."""
        tab = self.page.get_by_role("tab", name="Invoice Lines")
        if tab.count() == 0:
            tab = self.page.locator(f"{L.NOTEBOOK} a[name='{L.INVOICE_LINES_TAB_NAME}']")
        if tab.count() > 0:
            try:
                tab.first.click()
            except (PlaywrightTimeoutError, AssertionError):
                pass  # already active / not clickable — the assertion below governs
        expect(self.page.locator(L.LINES_TABLE).first).to_be_visible()

    # -- DOM fallback ------------------------------------------------------ #
    def read_lines_from_dom(self) -> list[dict]:
        """Resilient DOM fallback if the RPC payload lacked nested line records."""
        rows = self.page.locator(L.LINE_ROW)
        out: list[dict] = []
        index = 0
        for i in range(rows.count()):
            row = rows.nth(i)

            def cell(field: str) -> str:
                node = row.locator(f"td[name='{field}']")
                return node.first.inner_text().strip() if node.count() > 0 else ""

            product = cell(L.LINE_FIELD["product"]).split("\n")[0].strip()
            qty = _parse_number(cell(L.LINE_FIELD["quantity"]))
            if not product and qty is None:
                continue  # section / note / empty row
            subtotal = _parse_number(cell(L.LINE_FIELD["subtotal"])) or 0.0
            total = _parse_number(cell(L.LINE_FIELD["total"]))
            out.append(
                {
                    "line_index": index,
                    "product": product or "(unnamed)",
                    "description": cell(L.LINE_FIELD["description"]) or None,
                    "quantity": qty or 0.0,
                    "unit_price": _parse_number(cell(L.LINE_FIELD["unit_price"])) or 0.0,
                    "taxes": [t for t in cell(L.LINE_FIELD["taxes"]).replace("\n", " ").split() if t],
                    "subtotal": subtotal,
                    "total": total if total is not None else subtotal,
                    "currency": None,
                    "source": "dom",
                }
            )
            index += 1
        return out


def _parse_number(text: str) -> Optional[float]:
    """Parse a localized monetary/quantity string ('1,234.56', '1.234,56 €')."""
    if not text:
        return None
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ",.-")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        # The right-most separator is the decimal point.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Lone comma: treat as decimal if it looks like one (2 trailing digits).
        cleaned = cleaned.replace(",", ".") if len(cleaned.split(",")[-1]) in (1, 2) else cleaned.replace(",", "")
    return to_number(cleaned)
