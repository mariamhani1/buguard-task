"""API-key auth and rate-limit tests (multi-tenant trust anchor)."""
import pytest
from fastapi import HTTPException

import config
import auth
from auth import require_org, rate_limited


@pytest.fixture(autouse=True)
def known_keys(monkeypatch):
    monkeypatch.setitem(config.API_KEYS, "test-key", "org_test")


async def test_require_org_resolves_org_from_key():
    assert await require_org("test-key") == "org_test"


async def test_require_org_rejects_missing_key():
    with pytest.raises(HTTPException) as exc:
        await require_org(None)
    assert exc.value.status_code == 401


async def test_require_org_rejects_unknown_key():
    with pytest.raises(HTTPException) as exc:
        await require_org("bogus")
    assert exc.value.status_code == 401


async def test_rate_limit_blocks_after_budget():
    dep = rate_limited(2)
    org = "org_ratelimit_test"
    auth._request_log.pop(org, None)
    assert await dep(org_id=org) == org
    assert await dep(org_id=org) == org
    with pytest.raises(HTTPException) as exc:
        await dep(org_id=org)
    assert exc.value.status_code == 429
