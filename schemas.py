from typing import List, Dict, Any, Optional, Literal

from pydantic import BaseModel, Field, ConfigDict, AliasChoices

from models import AssetStatus, AssetType


# --- Ingest -----------------------------------------------------------------
class AssetImport(BaseModel):
    """Validated shape of one inbound asset record. Used to validate each row of a
    bulk import individually so a malformed record fails on its own without aborting
    the batch."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: AssetType
    value: str
    status: AssetStatus = AssetStatus.active
    source: str = "import"
    tags: List[str] = Field(default_factory=list)
    # Accept either "metadata" (spec/sample) or "metadata_".
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata", "metadata_"),
    )
    parent: Optional[str] = None
    covers: Optional[str] = None


# --- Analysis I/O -----------------------------------------------------------
class AnalyzeRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class AnalyzeResponse(BaseModel):
    result: str


# --- Structured tool outputs (enforced via llm.with_structured_output) -------
class RiskAssessment(BaseModel):
    risk_score: int = Field(ge=0, le=100, description="0 (no risk) to 100 (critical).")
    summary: str = Field(description="Concise, grounded risk summary.")
    factors: List[str] = Field(default_factory=list, description="Specific drivers of the score.")


class Enrichment(BaseModel):
    environment: Literal["prod", "staging", "dev", "unknown"]
    category: str = Field(description="Functional category, e.g. api, web, mail, database, infra.")
    criticality: Literal["low", "medium", "high"]
