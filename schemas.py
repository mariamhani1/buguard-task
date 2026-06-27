from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from models import AssetStatus

class AssetImport(BaseModel):
    id: str
    type: str
    value: str
    status: Optional[AssetStatus] = AssetStatus.active
    source: Optional[str] = "import"
    tags: Optional[List[str]] = []
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, alias="metadata_")
    parent: Optional[str] = None
    covers: Optional[str] = None

class AnalyzeRequest(BaseModel):
    prompt: str