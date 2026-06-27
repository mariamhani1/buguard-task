import enum
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class AssetStatus(str, enum.Enum):
    active = "active"
    stale = "stale"
    archived = "archived"

class Asset(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True, index=True)
    org_id = Column(String, primary_key=True, index=True) # Multi-tenancy bonus
    type = Column(String, index=True, nullable=False)
    value = Column(String, index=True, nullable=False)
    status = Column(Enum(AssetStatus), default=AssetStatus.active, nullable=False)
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    source = Column(String, nullable=False)
    tags = Column(ARRAY(String), default=[], nullable=False)
    metadata_ = Column("metadata", JSONB, default={}, nullable=False)

class AssetRelationship(Base):
    __tablename__ = "asset_relationships"

    id = Column(String, primary_key=True)
    org_id = Column(String, index=True, nullable=False) # Multi-tenancy bonus
    source_asset_id = Column(String, nullable=False)
    target_asset_id = Column(String, nullable=False)
    relationship_type = Column(String, nullable=False)