"""Database-backed integration tests for dedup, conflict-merge, idempotent
relationships, lifecycle revive, multi-tenant isolation, and filtering.

These require a reachable PostgreSQL (JSONB/ARRAY/ON CONFLICT are Postgres-specific).
Set TEST_DATABASE_URL (or DATABASE_URL); the module skips cleanly if it cannot connect,
so the pure unit suite always runs. Inside `docker compose`, run:
    docker compose exec web pytest
"""
import json
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import crud
import services
from models import Base, Asset, AssetRelationship

DB_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
SEED = json.loads((Path(__file__).parent.parent / "seed" / "assets.json").read_text())


@pytest_asyncio.fixture
async def sessionmaker_fx():
    # Function-scoped: the engine is created in the same event loop the test runs in,
    # so asyncpg connections are never shared across loops.
    if not DB_URL:
        pytest.skip("No TEST_DATABASE_URL/DATABASE_URL configured")
    engine = create_async_engine(DB_URL, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _org() -> str:
    return f"itest_{uuid.uuid4().hex[:8]}"


async def _count(sm, model, org):
    async with sm() as db:
        return (await db.execute(select(func.count()).select_from(model).where(model.org_id == org))).scalar()


async def test_idempotent_import_assets_and_relationships(sessionmaker_fx):
    org = _org()
    async with sessionmaker_fx() as db:
        ok1, fail1, _ = await crud.bulk_upsert_assets(db, SEED, org)
    assets_after_1 = await _count(sessionmaker_fx, Asset, org)
    rels_after_1 = await _count(sessionmaker_fx, AssetRelationship, org)

    async with sessionmaker_fx() as db:
        ok2, fail2, _ = await crud.bulk_upsert_assets(db, SEED, org)
    assets_after_2 = await _count(sessionmaker_fx, Asset, org)
    rels_after_2 = await _count(sessionmaker_fx, AssetRelationship, org)

    assert ok1 == ok2 == len(SEED) and fail1 == fail2 == 0
    assert assets_after_1 == assets_after_2 == len(SEED)        # no duplicate assets
    assert rels_after_1 == rels_after_2 and rels_after_1 > 0     # no duplicate relationships


async def test_conflict_merges_metadata_and_tags(sessionmaker_fx):
    org = _org()
    rec_v1 = [{"id": "m1", "type": "subdomain", "value": "api.example.com",
               "tags": ["prod"], "metadata": {"x": 1}, "source": "scanA"}]
    rec_v2 = [{"id": "m1", "type": "subdomain", "value": "api.example.com",
               "tags": ["production"], "metadata": {"y": 2}, "source": "scanB"}]
    async with sessionmaker_fx() as db:
        await crud.bulk_upsert_assets(db, rec_v1, org)
    async with sessionmaker_fx() as db:
        await crud.bulk_upsert_assets(db, rec_v2, org)
        asset = await services.get_asset_by_value(db, org, "api.example.com")

    assert set(asset["tags"]) == {"prod", "production"}        # tags unioned, not dropped
    assert asset["metadata"]["x"] == 1 and asset["metadata"]["y"] == 2  # metadata merged
    assert asset["first_seen"] is not None


async def test_stale_asset_revives_on_resight(sessionmaker_fx):
    org = _org()
    async with sessionmaker_fx() as db:
        await crud.bulk_upsert_assets(
            db, [{"id": "r1", "type": "subdomain", "value": "old.example.com", "status": "stale"}], org
        )
        first = await services.get_asset_by_value(db, org, "old.example.com")
    assert first["status"] == "stale"
    async with sessionmaker_fx() as db:
        await crud.bulk_upsert_assets(
            db, [{"id": "r1", "type": "subdomain", "value": "old.example.com"}], org
        )
        again = await services.get_asset_by_value(db, org, "old.example.com")
    assert again["status"] == "active"  # re-sighting revives


async def test_malformed_records_do_not_abort_batch(sessionmaker_fx):
    org = _org()
    batch = [
        {"id": "good", "type": "domain", "value": "ok.com"},
        {"id": "bad", "type": "not-a-type", "value": "x"},  # invalid enum
        "totally-not-an-object",
    ]
    async with sessionmaker_fx() as db:
        ok, fail, errors = await crud.bulk_upsert_assets(db, batch, org)
    assert ok == 1 and fail == 2 and len(errors) == 2


async def test_multi_tenant_isolation(sessionmaker_fx):
    org_a, org_b = _org(), _org()
    async with sessionmaker_fx() as db:
        await crud.bulk_upsert_assets(db, SEED, org_a)
    async with sessionmaker_fx() as db:
        await crud.bulk_upsert_assets(
            db, [{"id": "b1", "type": "domain", "value": "secret.globex.com"}], org_b
        )
    async with sessionmaker_fx() as db:
        a_items, _ = await services.list_assets(db, org_a, limit=200)
        b_items, _ = await services.list_assets(db, org_b, limit=200)
    a_values = {i["value"] for i in a_items}
    assert "secret.globex.com" not in a_values            # org B leaks nowhere into org A
    assert {i["value"] for i in b_items} == {"secret.globex.com"}


async def test_filters_tags_value_and_expired_certs(sessionmaker_fx):
    org = _org()
    async with sessionmaker_fx() as db:
        await crud.bulk_upsert_assets(db, SEED, org)
        by_tag, _ = await services.list_assets(db, org, tags=["production"], limit=200)
        by_value, _ = await services.list_assets(db, org, value_contains="staging", limit=200)
        expired, _ = await services.list_assets(db, org, cert_expired=True, limit=200)

    assert "api.example.com" in {i["value"] for i in by_tag}
    assert all("staging" in i["value"] for i in by_value)
    expired_values = {i["value"] for i in expired}
    assert "CN=api.example.com" in expired_values          # cert1 expired (2025-01-02)
    assert "CN=staging.example.com" not in expired_values   # cert2 not expired
