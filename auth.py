"""Lightweight API-key authentication and per-tenant rate limiting.

The API key is the trust anchor: the organization a caller may read/write is resolved
from their key server-side. Clients never assert their own org id, which closes the
multi-tenant isolation bypass."""
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException

import config

# Per-org sliding-window request log for the /analyze rate limiter. In-memory and
# per-process (sufficient for this single-instance demo; a shared store such as Redis
# would be used for a real multi-instance deployment).
_request_log: dict[str, deque] = defaultdict(deque)


async def require_org(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    """Resolve the caller's organization from their API key, or reject the request."""
    if not x_api_key or x_api_key not in config.API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API key (X-API-Key).")
    return config.API_KEYS[x_api_key]


def rate_limited(max_per_min: int):
    """Dependency factory: authenticate, then enforce a per-org request budget."""

    async def dependency(org_id: str = Depends(require_org)) -> str:
        now = time.monotonic()
        log = _request_log[org_id]
        while log and now - log[0] > 60:
            log.popleft()
        if len(log) >= max_per_min:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")
        log.append(now)
        return org_id

    return dependency


# Pre-built dependency for the (paid) analysis endpoint.
analyze_guard = rate_limited(config.ANALYZE_RATE_LIMIT_PER_MIN)
