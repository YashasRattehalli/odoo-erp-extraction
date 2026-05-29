"""Centralized, resilient locators for the Odoo OWL web client.

Verified empirically against odoo.com (Odoo SaaS 19.3). The locator priority,
applied top-down and never inverted, is:

    1. semantic ``[name='<field>']`` attributes  (rendered from OWL ``t-att-name``,
       equal to the model field name, stable across versions and languages)
    2. ARIA roles / accessible names              (``get_by_role(...)``)
    3. stable framework classes                   (``.o_data_row``, ``.o_notebook`` …)
    4. scoped visible text                        (``:has-text()`` / ``get_by_text``)
    5. structural relative XPath                  (last resort)

We NEVER target generated OWL ids/hashes, Bootstrap utility classes, or
``:nth-child``. Element handles are never cached — every read re-queries, because
OWL re-renders replace DOM nodes on each reactive update.
"""
from __future__ import annotations

# -- Authentication --------------------------------------------------------- #
LOGIN_INPUT = "input[name='login']"
PASSWORD_INPUT = "input[name='password']"
SUBMIT_BUTTON = "button[type='submit']"
LOGIN_ERROR = ".alert-danger"
APP_SHELL = ".o_main_navbar, .o_action_manager"

# -- Action / list view ----------------------------------------------------- #
# Deep-link by action xml-id; works on both the modern (/odoo/...) and legacy
# (/web#action=...) URL schemes. The script also detects the active scheme.
ACTION_XMLID = "account.action_move_out_invoice_type"
LIST_RENDERER = ".o_list_renderer"
LIST_VIEW = ".o_list_view"
DATA_ROW = "tr.o_data_row"
DATA_CELL = "td.o_data_cell"
# The out_invoice tree shows the customer under invoice_partner_display_name;
# partner_id is the fallback for other invoice trees.
PARTNER_CELL = "td[name='invoice_partner_display_name'], td[name='partner_id']"
LOADING_OVERLAY = ".o_loading, .o_blockUI"

# -- Search / filter --------------------------------------------------------- #
SEARCH_FACET = ".o_searchview_facet"
FACET_REMOVE = ".o_searchview_facet .o_facet_remove"
SEARCH_DROPDOWN_TOGGLER = ".o_searchview_dropdown_toggler"
SEARCH_MENU = ".o-dropdown--menu"
POSTED_FILTER_LABEL = "Posted"

# -- Form view --------------------------------------------------------------- #
FORM_VIEW = ".o_form_view"
# data-value on the status button is the untranslated technical value ('posted').
STATUSBAR_CURRENT = ".o_statusbar_status .o_arrow_button_current"
NOTEBOOK = ".o_notebook"
ACTIVE_TAB_PANE = ".o_notebook .tab-pane.active"
LINES_TABLE = ".o_notebook .tab-pane.active .o_list_renderer"
LINE_ROW = ".o_notebook .tab-pane.active .o_list_renderer tr.o_data_row"
INVOICE_LINES_TAB_NAME = "invoice_tab"  # OWL notebook page name for "Invoice Lines"

# -- Line cell field names (model field == DOM name attribute) --------------- #
# Used for the resilient DOM-fallback path; the RPC payload is the primary source.
LINE_FIELD = {
    "product": "product_id",
    "description": "name",
    "quantity": "quantity",
    "unit_price": "price_unit",
    "taxes": "tax_ids",
    "subtotal": "price_subtotal",
    "total": "price_total",  # often an optional/hidden column -> RPC is authoritative
}
