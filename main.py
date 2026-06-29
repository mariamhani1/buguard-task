import logging
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import config
from models import Base
from crud import bulk_upsert_assets
from schemas import AnalyzeRequest, AnalyzeResponse
from auth import require_org, analyze_guard
import services
from agent import get_analysis_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("darkatlas")

engine = create_async_engine(config.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        yield session


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_all is convenient for the demo; Alembic migrations are the production path.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="DarkAtlas Asset Management - AI Track",
    description="Minimal asset inventory API with a grounded LangChain analysis layer.",
    lifespan=lifespan,
)


@app.get("/health", tags=["Ops"])
async def health():
    return {"status": "ok"}


@app.post("/api/v1/assets/import", tags=["Assets"])
async def import_assets(
    payload: List[Any],
    response: Response,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(require_org),
):
    """Bulk import. Each record is validated independently; malformed records are
    reported without aborting the batch. Idempotent: re-importing does not duplicate."""
    if not isinstance(payload, list) or not payload:
        raise HTTPException(status_code=422, detail="Body must be a non-empty JSON array of asset records.")
    if len(payload) > config.MAX_IMPORT_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"Batch too large: {len(payload)} > MAX_IMPORT_BATCH ({config.MAX_IMPORT_BATCH}).",
        )

    successful, failed, errors = await bulk_upsert_assets(db, payload, org_id)
    if successful == 0:
        response.status_code = 422
        status = "failed"
    elif failed > 0:
        response.status_code = 207
        status = "partial_success"
    else:
        status = "completed"
    return {"status": status, "successful_records": successful, "failed_records": failed, "errors": errors}


@app.get("/api/v1/assets", tags=["Assets"])
async def list_assets(
    type: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[List[str]] = Query(default=None),
    value_contains: Optional[str] = None,
    limit: int = Query(default=config.QUERY_DEFAULT_LIMIT, ge=1, le=config.QUERY_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(require_org),
):
    """Org-scoped, filtered, paginated listing (also makes ingest independently verifiable)."""
    items, total = await services.list_assets(
        db, org_id, type=type, status=status, tags=tag, value_contains=value_contains,
        limit=limit, offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/v1/assets/{value:path}/graph", tags=["Assets"])
async def asset_graph(
    value: str,
    db: AsyncSession = Depends(get_db),
    org_id: str = Depends(require_org),
):
    """Fetch an asset together with its related assets (the relationship graph around it)."""
    graph = await services.get_neighbors(db, org_id, value)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Asset '{value}' not found.")
    return graph


@app.post("/api/v1/analyze", response_model=AnalyzeResponse, tags=["AI Analysis Engine"])
async def analyze_attack_surface(
    request: AnalyzeRequest,
    org_id: str = Depends(analyze_guard),
):
    """Unified, grounded AI endpoint: natural-language query, risk scoring, enrichment,
    and reporting via a LangChain tool-calling agent."""
    try:
        agent_executor = get_analysis_agent(async_session, org_id)
        result = await agent_executor.ainvoke({"input": request.prompt})
        return AnalyzeResponse(result=result["output"])
    except RuntimeError as exc:  # configuration error (e.g. missing API key)
        logger.error("Analyze configuration error: %s", exc)
        raise HTTPException(status_code=503, detail="AI analysis is not configured on this server.")
    except Exception as exc:  # noqa: BLE001 - log detail server-side, return generic message
        logger.exception("Analyze failed for org=%s", org_id)
        raise HTTPException(status_code=502, detail="AI analysis failed. Please retry later.")
