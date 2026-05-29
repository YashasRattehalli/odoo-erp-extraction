"""Tests for the in-memory PHI log-sanitization engine."""
import logging

from odoo_extractor.sanitization import PhiRedactionFilter, PhiRedactor


def test_denylist_removes_exact_token():
    r = PhiRedactor()
    r.register_secret("Deco Addict")
    out = r.scrub("Opened the invoice for Deco Addict today")
    assert "Deco Addict" not in out
    assert "[REDACTED:PHI]" in out


def test_denylist_is_case_insensitive():
    r = PhiRedactor()
    r.register_secret("Office Chair")
    assert "Office Chair" not in r.scrub("sold an OFFICE CHAIR")


def test_short_tokens_are_ignored():
    r = PhiRedactor(min_token_len=3)
    r.register_secret("AB")  # too short -> ignored to avoid over-redaction
    assert r.scrub("ABABAB") == "ABABAB"


def test_structured_pii_patterns():
    r = PhiRedactor()
    assert "[REDACTED:EMAIL]" in r.scrub("mail me at jane.doe@example.com")
    assert "[REDACTED:SSN]" in r.scrub("ssn 123-45-6789")
    assert "[REDACTED:INVOICE_NO]" in r.scrub("ref INV/2026/00001 posted")


def test_phone_is_conservative_and_spares_dates_and_hashes():
    r = PhiRedactor()
    # ISO date and hex hash must survive untouched
    assert r.scrub("2026-05-29") == "2026-05-29"
    assert r.scrub("dc149831b8a07a77") == "dc149831b8a07a77"
    # explicit international phone is redacted
    assert "[REDACTED:PHONE]" in r.scrub("call +1 415 555 0123 now")


def test_url_allowlist_preserves_instance_but_redacts_others():
    r = PhiRedactor(allow=["https://acme.odoo.com"])
    out = r.scrub("from https://acme.odoo.com to https://patient-site.example.com/p")
    assert "https://acme.odoo.com" in out
    assert "patient-site.example.com" not in out


def test_filter_renders_args_then_clears_them():
    r = PhiRedactor()
    r.register_secret("Deco Addict")
    flt = PhiRedactionFilter(r)
    rec = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "customer %s ordered %d units", ("Deco Addict", 3), None
    )
    assert flt.filter(rec) is True
    rendered = rec.getMessage()
    assert "Deco Addict" not in rendered          # the load-bearing guarantee
    assert rec.args == ()                          # args dropped so it cannot resurface
    assert "[REDACTED:PHI]" in rendered


def test_filter_scrubs_exception_traceback():
    r = PhiRedactor()
    r.register_secret("Deco Addict")
    flt = PhiRedactionFilter(r)
    try:
        raise ValueError("failure involving Deco Addict record")
    except ValueError:
        import sys
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    flt.filter(rec)
    assert rec.exc_text is not None
    assert "Deco Addict" not in rec.exc_text


def test_scrub_never_raises_on_non_string():
    r = PhiRedactor()
    assert isinstance(r.scrub(12345), str)
    assert r.scrub(None) == ""
