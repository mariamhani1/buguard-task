"""One-command dataset loader.

Seeds two tenants (org_acme and org_globex) through the public import endpoint so the
analysis examples in the README are reproducible and multi-tenant isolation is
demonstrable. Idempotent: safe to run repeatedly.

Usage (API must be running):
    python seed.py
    BASE_URL=http://localhost:8000 python seed.py
"""
import json
import os
import sys
from pathlib import Path

import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
SEED_DIR = Path(__file__).parent / "seed"

# (file, api-key) pairs. Keys map to org_acme / org_globex via the default DEV API_KEYS.
DATASETS = [
    ("assets.json", os.getenv("ACME_API_KEY", "dev-key-acme")),
    ("assets_globex.json", os.getenv("GLOBEX_API_KEY", "dev-key-globex")),
]


def load(file_name: str, api_key: str) -> None:
    path = SEED_DIR / file_name
    records = json.loads(path.read_text())
    resp = httpx.post(
        f"{BASE_URL}/api/v1/assets/import",
        headers={"X-API-Key": api_key},
        json=records,
        timeout=30,
    )
    resp.raise_for_status() if resp.status_code in (200, 207) else None
    body = resp.json()
    print(
        f"{file_name:24s} -> HTTP {resp.status_code} "
        f"({body.get('successful_records')} ok, {body.get('failed_records')} failed)"
    )
    for err in body.get("errors", []):
        print(f"    ! {err}")


def main() -> int:
    print(f"Seeding {BASE_URL} ...")
    try:
        for file_name, api_key in DATASETS:
            load(file_name, api_key)
    except httpx.HTTPError as exc:
        print(f"Seeding failed: {exc}", file=sys.stderr)
        print("Is the API running? Try: docker compose up --build -d", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
