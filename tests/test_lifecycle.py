"""Deterministic lifecycle/risk signal tests (grounding substrate, no LLM)."""
from datetime import datetime, timezone

from lifecycle import parse_date, cert_status, is_eol_technology, asset_risk_signals

NOW = datetime(2026, 6, 29, tzinfo=timezone.utc)


def test_parse_date_various_and_invalid():
    assert parse_date("2025-01-02").year == 2025
    assert parse_date("2025-01-02T10:00:00Z").year == 2025
    assert parse_date(None) is None
    assert parse_date("not-a-date") is None


def test_cert_status_expired_soon_valid_unknown():
    assert cert_status("2025-01-02", now=NOW) == "expired"
    assert cert_status("2026-07-05", days_soon=30, now=NOW) == "expiring_soon"
    assert cert_status("2026-12-31", days_soon=30, now=NOW) == "valid"
    assert cert_status(None, now=NOW) == "unknown"


def test_is_eol_technology_marker_and_flag():
    assert is_eol_technology("OpenSSL", {"version": "1.0.2"}) is True   # known-EOL marker
    assert is_eol_technology("nginx", {"version": "1.18", "eol": True}) is True  # explicit flag
    assert is_eol_technology("nginx", {"version": "1.18"}) is False


def test_asset_risk_signals_sensitive_service():
    asset = {"type": "service", "value": "3389/tcp", "status": "active", "metadata": {"port": 3389}}
    sig = asset_risk_signals(asset)
    assert sig["sensitive_service"] is True
    assert sig["port"] == 3389


def test_asset_risk_signals_certificate_expired():
    asset = {"type": "certificate", "value": "CN=x", "status": "active", "metadata": {"expires": "2000-01-01"}}
    sig = asset_risk_signals(asset)
    assert sig["certificate_expired"] is True
