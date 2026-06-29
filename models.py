import enum
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Enum, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetType(str, enum.Enum):
    domain = "domain"
    subdomain = "subdomain"
    ip_address = "ip_address"
    service = "service"
    certificate = "certificate"
    technology = "technology"


class AssetStatus(str, enum.Enum):
    active = "active"
    stale = "stale"
    archived = "archived"


class Asset(Base):
    __tablename__ = "assets"

    # (id, org_id) is the composite primary key: ids are only unique within a tenant,
    # and ON CONFLICT (id, org_id) drives idempotent upserts.
    id = Column(String, primary_key=True, index=True)
    org_id = Column(String, primary_key=True, index=True)
    type = Column(Enum(AssetType, name="asset_type"), index=True, nullable=False)
    value = Column(String, index=True, nullable=False)
    status = Column(Enum(AssetStatus, name="asset_status"), default=AssetStatus.active, index=True, nullable=False)
    first_seen = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    source = Column(String, nullable=False, default="import")
    tags = Column(ARRAY(String), default=list, nullable=False)
    # The python attribute is `metadata_` because `metadata` is reserved by the
    # declarative Base; the actual column name remains "metadata".
    metadata_ = Column("metadata", JSONB, default=dict, nullable=False)


class AssetRelationship(Base):
    __tablename__ = "asset_relationships"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "source_asset_id", "target_asset_id", "relationship_type",
            name="uq_relationship_edge",
        ),
    )

    # id is a deterministic uuid5 of the edge tuple (see crud.relationship_id), so
    # re-importing the same edge is a true no-op rather than a duplicate row.
    id = Column(String, primary_key=True)
    org_id = Column(String, index=True, nullable=False)
    source_asset_id = Column(String, nullable=False)
    target_asset_id = Column(String, nullable=False)
    relationship_type = Column(String, nullable=False)
