from fastapi import FastAPI, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import List, Dict, Any
import os
from models import Base
from crud import bulk_upsert_assets

# Database Setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:securepassword123@db:5432/darkatlas_asm")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with async_session() as session:
        yield session

app = FastAPI(title="DarkAtlas Asset Management - AI Track")

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.post("/api/v1/assets/import")
async def import_assets(
    payload: List[Dict[str, Any]], 
    db: AsyncSession = Depends(get_db),
    x_org_id: str = Header(default="default_org", alias="X-Organization-ID")
):
    """Bulk import endpoint with idempotency and multi-tenant scoping."""
    successful, failed, errors = await bulk_upsert_assets(db, payload, x_org_id)
    return {
        "status": "completed" if failed == 0 else "partial_success",
        "successful_records": successful,
        "failed_records": failed,
        "errors": errors
    }

# Placeholder for the Analyze endpoint (we will build this in agent.py next)
@app.post("/api/v1/analyze")
async def analyze_data(prompt: dict):
    return {"message": "LangChain Agent endpoint coming next."}