"""Typed, validated domain models for the extraction output.

These models define the *contract* of the JSON artifact the tool emits. Using
Pydantic gives us (a) validation/coercion of the values we pull from Odoo,
(b) a single source of truth for the output schema, and (c) deterministic
serialization.

The per-line ``tax_amount`` is a *computed* field deliberately derived as
``price_total - price_subtotal``. Odoo computes both server-side, so their
difference is the authoritative per-line tax — never parsed or guessed.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


def round_money(value: Optional[float], places: int = 2) -> Optional[float]:
    """Round a monetary value half-up to ``places`` decimals.

    Uses ``Decimal`` to avoid binary float drift (e.g. ``0.1 + 0.2``). Returns
    ``None`` unchanged so optional fields stay optional.
    """
    if value is None:
        return None
    quant = Decimal(1).scaleb(-places)  # 10**-places
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


class InvoiceLine(BaseModel):
    """A single billing line extracted from an invoice's *Invoice Lines* tab."""

    model_config = ConfigDict(extra="forbid")

    line_index: int = Field(..., ge=0, description="0-based position within the invoice.")
    product: str = Field(..., description="Product display name.")
    description: Optional[str] = Field(None, description="Line label / description.")
    quantity: float = Field(..., description="Billed quantity.")
    unit_price: float = Field(..., description="Unit price before tax (price_unit).")
    taxes: list[str] = Field(default_factory=list, description="Human-readable tax tag(s).")
    subtotal: float = Field(..., description="Line net, pre-tax (price_subtotal).")
    total: float = Field(..., description="Line total, tax-inclusive (price_total).")
    currency: Optional[str] = Field(None, description="ISO currency code, if known.")
    source: str = Field("json-rpc", description="Provenance: 'json-rpc' or 'dom'.")

    @computed_field(description="Derived per-line tax = total - subtotal (server-sourced values).")
    @property
    def tax_amount(self) -> float:
        return round_money(self.total - self.subtotal)


class InvoiceHeader(BaseModel):
    """Header-level facts about the invoice (the ``account.move`` record)."""

    model_config = ConfigDict(extra="forbid")

    invoice_number: Optional[str] = None
    customer: str
    invoice_date: Optional[str] = None
    state: Optional[str] = None
    currency: Optional[str] = None
    amount_untaxed: Optional[float] = None
    amount_tax: Optional[float] = None
    amount_total: Optional[float] = None
    source: str = "json-rpc"


class IntegrityChecks(BaseModel):
    """Self-verifying integrity controls (maps to HIPAA 45 CFR 164.312(c))."""

    model_config = ConfigDict(extra="forbid")

    line_count: int
    line_tax_sum: float
    line_total_sum: float
    line_tax_sum_equals_header: Optional[bool] = None
    line_total_sum_equals_amount_total: Optional[bool] = None


class ExtractionMetadata(BaseModel):
    """Non-PHI provenance metadata about the extraction run."""

    model_config = ConfigDict(extra="forbid")

    tool: str = "odoo-erp-extraction"
    tool_version: str
    extracted_at: str  # ISO-8601 UTC
    source: str  # e.g. "json-rpc:account.move/web_read" or "dom"
    instance_url: Optional[str] = None
    url_scheme: Optional[str] = None  # "modern" (/odoo/...) | "legacy" (/web#...)
    deidentified: bool = False
    integrity_sha256: Optional[str] = None


class ExtractionResult(BaseModel):
    """The complete, standardized output document."""

    model_config = ConfigDict(extra="forbid")

    metadata: ExtractionMetadata
    invoice: InvoiceHeader
    lines: list[InvoiceLine]
    checks: IntegrityChecks

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize to a stable, human-readable JSON string."""
        return self.model_dump_json(indent=indent)
