import uuid
from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from models import Asset, AssetRelationship, AssetStatus
from datetime import datetime

async def bulk_upsert_assets(db: AsyncSession, raw_assets: List[Dict[str, Any]], org_id: str) -> Tuple[int, int, List[str]]:
    successful = 0
    failed = 0
    error_messages = []
    
    for idx, raw_data in enumerate(raw_assets):
        try:
            # 1. Validate mandatory fields to prevent batch crashes
            if not raw_data.get("id") or not raw_data.get("type") or not raw_data.get("value"):
                raise ValueError("Missing mandatory fields: id, type, or value")

            # 2. Build the Insert Statement
            stmt = insert(Asset).values(
                id=str(raw_data["id"]),
                org_id=org_id,
                type=str(raw_data["type"]),
                value=str(raw_data["value"]),
                status=AssetStatus.active,
                first_seen=datetime.utcnow(),
                last_seen=datetime.utcnow(),
                source=str(raw_data.get("source", "import")),
                tags=list(raw_data.get("tags", [])),
                metadata_=dict(raw_data.get("metadata", {}))
            )
            
            # 3. Idempotent Upsert Strategy (Handle Duplicates & Re-appearing assets)
            update_stmt = stmt.on_conflict_do_update(
                index_elements=['id', 'org_id'], # Compound unique key
                set_={
                    "last_seen": datetime.utcnow(),
                    "status": AssetStatus.active, # Re-appearing assets become active again
                    "metadata_": stmt.excluded.metadata_ # Simple replacement for metadata
                }
            )
            await db.execute(update_stmt)
            
            # 4. Handle Relationships (Graph)
            relationships = []
            if raw_data.get("parent"):
                relationships.append({"target": str(raw_data["parent"]), "type": "child_of"})
            if raw_data.get("covers"):
                relationships.append({"target": str(raw_data["covers"]), "type": "covers"})
                
            for rel in relationships:
                rel_stmt = insert(AssetRelationship).values(
                    id=str(uuid.uuid4()), org_id=org_id,
                    source_asset_id=str(raw_data["id"]), target_asset_id=rel["target"],
                    relationship_type=rel["type"]
                ).on_conflict_do_nothing()
                await db.execute(rel_stmt)

            successful += 1
        except Exception as e:
            failed += 1
            error_messages.append(f"Record {idx} failed: {str(e)}")
            
    await db.commit()
    return successful, failed, error_messages