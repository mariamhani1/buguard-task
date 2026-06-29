"""Grounded data-access layer. Every function is org-scoped and returns only rows
that actually exist in the database — this is the factual substrate the LLM tools are
allowed to talk about. No LLM calls happen here."""
from typing import Any, List, Optional, Tuple

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

import config
import lifecycle
from models import Asset, AssetRelationship, AssetType, AssetStatus


def asset_to_dict(a: Asset) -> dict:
    return {
        "id": a.id,
        "type": a.type.value if a.type else None,
        "value": a.value,
        "status": a.status.value if a.status else None,
        "source": a.source,
        "tags": list(a.tags or []),
        "first_seen": a.first_seen.isoformat() if a.first_seen else None,
        "last_seen": a.last_seen.isoformat() if a.last_seen else None,
        "metadata": a.metadata_ or {},
    }


def _coerce(enum_cls, value):
    """Coerce an LLM-supplied string to an enum member, or None if it is invalid."""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


async def list_assets(
    db: AsyncSession,
    org_id: str,
    *,
    type: Optional[str] = None,
    status: Optional[str] = None,
    tags: Optional[List[str]] = None,
    value_contains: Optional[str] = None,
    cert_expired: Optional[bool] = None,
    expiring_within_days: Optional[int] = None,
    limit: int = None,
    offset: int = 0,
) -> Tuple[List[dict], int]:
    """Org-scoped, filtered, paginated asset query.

    type/status/tags/value_contains are pushed into SQL; certificate date predicates
    are computed deterministically in Python (see lifecycle) over the matched set."""
    limit = config.QUERY_DEFAULT_LIMIT if limit is None else min(limit, config.QUERY_MAX_LIMIT)

    q = select(Asset).where(Asset.org_id == org_id)

    type_enum = _coerce(AssetType, type)
    if type is not None and type_enum is None:
        return [], 0  # unknown type -> no matches (do not silently ignore the filter)
    if type_enum is not None:
        q = q.where(Asset.type == type_enum)

    status_enum = _coerce(AssetStatus, status)
    if status is not None and status_enum is None:
        return [], 0
    if status_enum is not None:
        q = q.where(Asset.status == status_enum)

    if tags:
        q = q.where(Asset.tags.overlap(tags))  # ARRAY && : matches any of the tags
    if value_contains:
        q = q.where(Asset.value.ilike(f"%{value_contains}%"))

    rows = (await db.execute(q.order_by(Asset.value))).scalars().all()
    items = [asset_to_dict(a) for a in rows]

    # Date-based predicates (certificates) applied in Python on the matched set.
    if cert_expired is not None or expiring_within_days is not None:
        def keep(a: dict) -> bool:
            if a["type"] != "certificate":
                return False
            md = a.get("metadata") or {}
            expires = md.get("expires") or md.get("expiry") or md.get("not_after")
            status_str = lifecycle.cert_status(
                expires,
                days_soon=expiring_within_days if expiring_within_days is not None else config.EXPIRING_SOON_DAYS,
            )
            if cert_expired is True and status_str != "expired":
                return False
            if cert_expired is False and status_str == "expired":
                return False
            if expiring_within_days is not None and status_str not in ("expired", "expiring_soon"):
                return False
            return True

        items = [a for a in items if keep(a)]

    total = len(items)
    return items[offset: offset + limit], total


async def get_asset_by_value(db: AsyncSession, org_id: str, value: str) -> Optional[dict]:
    row = (
        await db.execute(
            select(Asset).where(Asset.org_id == org_id, Asset.value == value).limit(1)
        )
    ).scalars().first()
    return asset_to_dict(row) if row else None


async def apply_enrichment(db: AsyncSession, org_id: str, value: str, enrichment: dict) -> Optional[dict]:
    """Persist an enrichment result into the asset's metadata (grounded write-back)."""
    row = (
        await db.execute(
            select(Asset).where(Asset.org_id == org_id, Asset.value == value).limit(1)
        )
    ).scalars().first()
    if not row:
        return None
    merged = dict(row.metadata_ or {})
    merged["enrichment"] = enrichment
    # Promote a couple of high-value fields to top level for easy querying.
    for key in ("environment", "category", "criticality"):
        if key in enrichment:
            merged[key] = enrichment[key]
    row.metadata_ = merged
    await db.commit()
    await db.refresh(row)
    return asset_to_dict(row)


async def get_neighbors(db: AsyncSession, org_id: str, value: str) -> Optional[dict]:
    """Return an asset together with the assets it is related to (the graph around it)."""
    asset = (
        await db.execute(
            select(Asset).where(Asset.org_id == org_id, Asset.value == value).limit(1)
        )
    ).scalars().first()
    if not asset:
        return None

    edges = (
        await db.execute(
            select(AssetRelationship).where(
                AssetRelationship.org_id == org_id,
                or_(
                    AssetRelationship.source_asset_id == asset.id,
                    AssetRelationship.target_asset_id == asset.id,
                ),
            )
        )
    ).scalars().all()

    neighbor_ids = {e.source_asset_id for e in edges} | {e.target_asset_id for e in edges}
    neighbor_ids.discard(asset.id)
    neighbors = {}
    if neighbor_ids:
        rows = (
            await db.execute(
                select(Asset).where(Asset.org_id == org_id, Asset.id.in_(neighbor_ids))
            )
        ).scalars().all()
        neighbors = {r.id: asset_to_dict(r) for r in rows}

    return {
        "asset": asset_to_dict(asset),
        "relationships": [
            {
                "type": e.relationship_type,
                "direction": "out" if e.source_asset_id == asset.id else "in",
                "other": neighbors.get(
                    e.target_asset_id if e.source_asset_id == asset.id else e.source_asset_id,
                    {"id": e.target_asset_id if e.source_asset_id == asset.id else e.source_asset_id},
                ),
            }
            for e in edges
        ],
    }


async def build_report_context(db: AsyncSession, org_id: str, days_soon: int | None = None) -> dict:
    """Assemble a grounded, deterministic risk/inventory context for report generation.
    All risk facts (expired/expiring certs, sensitive services, EOL tech) are computed
    here so the model only narrates real findings."""
    rows = (
        await db.execute(
            select(Asset).where(Asset.org_id == org_id).order_by(Asset.type, Asset.value).limit(config.REPORT_MAX_ASSETS)
        )
    ).scalars().all()
    assets = [asset_to_dict(a) for a in rows]

    counts_by_type: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    expired_certs, expiring_certs, sensitive_services, eol_tech = [], [], [], []

    for a in assets:
        counts_by_type[a["type"]] = counts_by_type.get(a["type"], 0) + 1
        counts_by_status[a["status"]] = counts_by_status.get(a["status"], 0) + 1
        sig = lifecycle.asset_risk_signals(a, days_soon)
        if sig.get("certificate_expired"):
            expired_certs.append(a["value"])
        elif sig.get("certificate_expiring_soon"):
            expiring_certs.append(a["value"])
        if sig.get("sensitive_service"):
            sensitive_services.append({"value": a["value"], "port": sig.get("port")})
        if sig.get("end_of_life"):
            eol_tech.append(a["value"])

    return {
        "total_assets": len(assets),
        "counts_by_type": counts_by_type,
        "counts_by_status": counts_by_status,
        "expired_certificates": expired_certs,
        "expiring_soon_certificates": expiring_certs,
        "sensitive_exposed_services": sensitive_services,
        "end_of_life_technologies": eol_tech,
        "truncated": len(rows) >= config.REPORT_MAX_ASSETS,
        "assets": assets,
    }
