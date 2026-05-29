"""Root logging configuration wired to the PHI redaction filter.

The redaction filter is attached to *handlers* so every sink (console, optional
file) is guarded at the emit boundary — including log records propagated from
third-party libraries (Playwright, urllib3, ...). Only non-PHI metadata is ever
intended to be logged; the filter is the deterministic backstop.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from .sanitization import PhiRedactionFilter, PhiRedactor

# Third-party loggers we quiet down (their records still pass through the filter).
_NOISY = ("urllib3", "asyncio", "playwright", "httpx", "httpcore", "hpack", "websockets", "PIL")

_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: str = "INFO",
    *,
    redactor: Optional[PhiRedactor] = None,
    log_file: Optional[Path] = None,
    stream=None,
) -> PhiRedactor:
    """Configure the root logger with PHI-sanitizing handlers.

    Returns the :class:`PhiRedactor` so the caller can register runtime PHI
    tokens (customer/product names) on the *same* instance the filter uses.
    """
    redactor = redactor or PhiRedactor()
    root = logging.getLogger()
    root.setLevel(level.upper() if isinstance(level, str) else level)

    # Replace any pre-existing handlers so we fully control the sinks.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    phi_filter = PhiRedactionFilter(redactor)
    formatter = logging.Formatter(_FORMAT, _DATEFMT)

    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(phi_filter)
    root.addHandler(console)

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(phi_filter)  # the file sink is sanitized too
        root.addHandler(file_handler)

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    return redactor
