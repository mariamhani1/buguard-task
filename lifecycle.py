"""Deterministic, code-computed lifecycle/risk signals.

The LLM is never asked to decide whether a certificate is expired or a technology
is end-of-life. Those facts are derived here in Python from the stored asset data so
the analysis layer stays grounded in reality, not the model's guesses."""
from __future__ import annotations

from datetime import datetime, date, timezone, timedelta
from typing import Any, Optional

import config


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value: Any) -> Optional[datetime]:
    """Best-effort parse of a date/datetime into a timezone-aware UTC datetime.
    Returns None if the value is missing or unparseable (handled, never raised)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    s = str(value).strip()
    # Normalise a trailing Z to a form fromisoformat understands.
    s_norm = s.replace("Z", "+00:00")
    for parser in (
        lambda x: datetime.fromisoformat(x),
        lambda x: datetime.strptime(x, "%Y-%m-%d"),
        lambda x: datetime.strptime(x, "%Y/%m/%d"),
        lambda x: datetime.strptime(x, "%d-%m-%Y"),
    ):
        try:
            dt = parser(s_norm)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def cert_status(expires: Any, *, days_soon: int | None = None, now: datetime | None = None) -> str:
    """Classify a certificate expiry date: expired | expiring_soon | valid | unknown."""
    days_soon = config.EXPIRING_SOON_DAYS if days_soon is None else days_soon
    dt = parse_date(expires)
    if dt is None:
        return "unknown"
    now = now or now_utc()
    if dt < now:
        return "expired"
    if dt <= now + timedelta(days=days_soon):
        return "expiring_soon"
    return "valid"


def _service_port(value: str, metadata: dict) -> Optional[int]:
    """Extract a port from a service value like '443/tcp' or metadata.port."""
    if isinstance(metadata, dict) and metadata.get("port") is not None:
        try:
            return int(metadata["port"])
        except (ValueError, TypeError):
            pass
    if value and "/" in value:
        head = value.split("/", 1)[0].strip()
        if head.isdigit():
            return int(head)
    return None


def is_eol_technology(name: str, metadata: dict) -> bool:
    """A technology is EOL if metadata says so, or it matches a known-EOL marker."""
    if isinstance(metadata, dict) and metadata.get("eol") is True:
        return True
    version = (metadata or {}).get("version", "") if isinstance(metadata, dict) else ""
    marker = f"{name} {version}".strip().lower()
    return marker in config.KNOWN_EOL_TECH


def asset_risk_signals(asset: dict, days_soon: int | None = None) -> dict:
    """Compute deterministic risk signals for a single asset dict (from services.asset_to_dict)."""
    days_soon = config.EXPIRING_SOON_DAYS if days_soon is None else days_soon
    md = asset.get("metadata") or {}
    a_type = asset.get("type")
    signals: dict[str, Any] = {"type": a_type, "is_stale": asset.get("status") == "stale"}

    if a_type == "certificate":
        status = cert_status(md.get("expires") or md.get("expiry") or md.get("not_after"), days_soon=days_soon)
        signals["certificate_status"] = status
        signals["certificate_expired"] = status == "expired"
        signals["certificate_expiring_soon"] = status == "expiring_soon"
    elif a_type == "service":
        port = _service_port(asset.get("value", ""), md)
        signals["port"] = port
        signals["sensitive_service"] = port in config.SENSITIVE_PORTS if port else False
    elif a_type == "technology":
        signals["end_of_life"] = is_eol_technology(asset.get("value", ""), md)

    return signals
