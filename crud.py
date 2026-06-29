"""Bulk ingest: per-row validation, idempotent upsert, conflict merge, and
idempotent relationship edges. Merge logic lives in pure functions so it is unit
tested without a database."""
import uuid
from typing import List, Dict, Any, Tuple

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Asset, AssetRelationship, AssetStatus
from schemas import AssetImport
from lifecycle import now_utc

# Stable namespace so relationship ids are deterministic across imports/processes.
_REL_NS = uuid.UUID("6f4d2c2a-0b1e-5a3c-9d7f-1a2b3c4d5e6f")


# --- Pure merge helpers (unit tested) ---------------------------------------
def merge_metadata(old: Dict[str, Any] | None, new: Dict[str, Any] | None) -> Dict[str, Any]:
    """Merge two metadata payloads. Newer keys win; keys only present in the older
    payload are retained (so data from a second source is not lost)."""
    merged = dict(old or {})
    merged.update(new or {})
    return merged


def merge_tags(old: List[str] | None, new: List[str] | None) -> List[str]:
    """Union two tag lists, de-duplicated and stably ordered."""
    seen: list[str] = []
    for tag in list(old or []) + list(new or []):
        if tag not in seen:
            seen.append(tag)
    return seen


def relationship_id(org_id: str, source: str, target: str, rel_type: str) -> str:
    return str(uuid.uuid5(_REL_NS, f"{org_id}|{source}|{target}|{rel_type}"))


def _edges_from(record: AssetImport) -> list[dict]:
    edges = []
    if record.parent:
        edges.append({"target": record.parent, "type": "child_of"})
    if record.covers:
        edges.append({"target": record.covers, "type": "covers"})
    return edges


async def bulk_upsert_assets(
    db: AsyncSession, raw_assets: List[Dict[str, Any]], org_id: str
) -> Tuple[int, int, List[str]]:
    """Idempotent bulk import scoped to one organization.

    Returns (successful, failed, error_messages). Malformed rows are isolated and
    reported without aborting the batch."""
    errors: List[str] = []

    # 1) Validate each row independently and collapse intra-batch duplicates (last
    #    wins, merging metadata/tags) so the same id twice in one payload is one upsert.
    batch: dict[str, AssetImport] = {}
    order: list[str] = []
    for idx, raw in enumerate(raw_assets):
        if not isinstance(raw, dict):
            errors.append(f"Record {idx} failed: expected an object, got {type(raw).__name__}")
            continue
        try:
            record = AssetImport.model_validate(raw)
        except ValidationError as exc:
            errors.append(f"Record {idx} failed: {exc.errors()[0]['msg']} ({exc.error_count()} error(s))")
            continue
        if record.id in batch:
            prev = batch[record.id]
            record.metadata = merge_metadata(prev.metadata, record.metadata)
            record.tags = merge_tags(prev.tags, record.tags)
        else:
            order.append(record.id)
        batch[record.id] = record

    if not batch:
        return 0, len(errors), errors

    # 2) Load any existing rows for these ids in one query so the conflict merge uses
    #    the current stored metadata/tags.
    existing_rows = (
        await db.execute(
            select(Asset).where(Asset.org_id == org_id, Asset.id.in_(list(batch.keys())))
        )
    ).scalars().all()
    existing = {row.id: row for row in existing_rows}

    successful = 0
    now = now_utc()
    for asset_id in order:
        record = batch[asset_id]
        prior = existing.get(asset_id)
        merged_metadata = merge_metadata(prior.metadata_ if prior else {}, record.metadata)
        merged_tags = merge_tags(prior.tags if prior else [], record.tags)
        # First sighting honours the provided status; a re-sighting revives the asset
        # to active (a stale asset seen again should return to active).
        status = AssetStatus.active if prior else record.status

        stmt = insert(Asset).values(
            id=record.id,
            org_id=org_id,
            type=record.type,
            value=record.value,
            status=status,
            first_seen=now,
            last_seen=now,
            source=record.source,
            tags=merged_tags,
            metadata_=merged_metadata,
        ).on_conflict_do_update(
            index_elements=["id", "org_id"],
            set_={
                "last_seen": now,
                "status": status,
                "value": record.value,
                "tags": merged_tags,
                "metadata": merged_metadata,  # column name; first_seen deliberately omitted
            },
        )
        await db.execute(stmt)

        # 3) Idempotent relationship edges (deterministic id + unique constraint).
        for edge in _edges_from(record):
            rid = relationship_id(org_id, record.id, edge["target"], edge["type"])
            rel_stmt = insert(AssetRelationship).values(
                id=rid,
                org_id=org_id,
                source_asset_id=record.id,
                target_asset_id=edge["target"],
                relationship_type=edge["type"],
            ).on_conflict_do_nothing(index_elements=["id"])
            await db.execute(rel_stmt)

        successful += 1

    await db.commit()
    return successful, len(errors), errors
