"""Centralized, validated configuration.

All knobs are sourced (in precedence order) from explicit CLI arguments, then
environment variables (prefixed ``ODOO_``), then an optional ``.env`` file,
then the defaults below. Credentials are wrapped in :class:`SecretStr` so they
are never accidentally rendered in a repr, a log line, or a traceback.

The defaults point at a local instance; for the live demo, set ``ODOO_BASE_URL`` /
``ODOO_USERNAME`` / ``ODOO_PASSWORD`` (and optionally ``ODOO_DB``) to the
odoo.com trial (via ``.env`` or CLI flags).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for an extraction run."""

    model_config = SettingsConfigDict(
        env_prefix="ODOO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Target instance & credentials ------------------------------------ #
    base_url: str = Field(
        "http://localhost:8069",
        description="Root URL of the Odoo instance (local Docker or odoo.com trial).",
    )
    db: Optional[str] = Field(
        None,
        description="Database name. Optional for single-DB SaaS trials; set for local multi-DB.",
    )
    username: str = Field("admin", description="Login (email for odoo.com trials).")
    password: SecretStr = Field(SecretStr("admin"), description="Account password.")

    # -- What to extract --------------------------------------------------- #
    target_customer: str = Field(
        "Deco Addict",
        description=(
            "Primary customer to locate. The task specifies 'Deco Addict' (the "
            "res_partner_2 demo record on Odoo <=15); on Odoo 16+ that record was "
            "relabelled 'Acme Corporation' (see customer_aliases)."
        ),
    )
    customer_aliases: list[str] = Field(
        default_factory=lambda: ["Acme Corporation"],
        description="Fallback names tried (in order) if target_customer yields no row.",
    )

    # -- Browser behaviour ------------------------------------------------- #
    headless: bool = Field(True, description="Run Chromium headless (False shows the browser).")
    slow_mo_ms: int = Field(
        0,
        ge=0,
        description=(
            "Playwright slow_mo (cosmetic only, for live demos). This is a global "
            "launch option, NOT an in-flow wait — the automation contains zero "
            "time.sleep / wait_for_timeout calls."
        ),
    )
    nav_timeout_ms: int = Field(45_000, ge=1_000, description="Navigation timeout (ms).")
    action_timeout_ms: int = Field(20_000, ge=1_000, description="Per-action/assertion timeout (ms).")
    user_data_dir: Path = Field(
        Path(".pw-profile"),
        description="Persistent browser profile dir (keeps the session cookie across runs).",
    )

    # -- Output & privacy -------------------------------------------------- #
    output_path: Path = Field(
        Path("output/invoice_extract.json"),
        description="The single file artifact: the standardized JSON extract.",
    )
    deidentify: bool = Field(
        False,
        description="Apply a HIPAA Safe Harbor (45 CFR 164.514(b)(2)) pass to the output.",
    )
    deid_salt: SecretStr = Field(
        SecretStr("change-me-per-deployment"),
        description="HMAC salt for de-identification surrogate keys.",
    )

    # -- Logging / sanitization ------------------------------------------- #
    log_level: str = Field("INFO", description="Root log level.")
    log_file: Optional[Path] = Field(
        None,
        description="Optional log file (also fully PHI-sanitized). Off by default.",
    )
    enable_presidio: bool = Field(
        False,
        description="Enable the Presidio NER backstop layer (deny-list+regex always run).",
    )

    @property
    def is_local(self) -> bool:
        """True when targeting a localhost instance (affects nav defaults)."""
        return "localhost" in self.base_url or "127.0.0.1" in self.base_url

    def safe_summary(self) -> dict:
        """A log-safe view of the configuration (no secrets, no PHI)."""
        return {
            "base_url": self.base_url,
            "db": self.db or "(default)",
            "username_set": bool(self.username),
            "headless": self.headless,
            "deidentify": self.deidentify,
            "presidio": self.enable_presidio,
            "output": str(self.output_path),
        }
