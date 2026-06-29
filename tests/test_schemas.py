"""Input-validation tests for AssetImport (malformed-record handling)."""
import pytest
from pydantic import ValidationError

from schemas import AssetImport
from models import AssetType, AssetStatus


def test_valid_record_with_metadata_alias():
    rec = AssetImport.model_validate(
        {"id": "a1", "type": "domain", "value": "example.com", "metadata": {"k": "v"}}
    )
    assert rec.type is AssetType.domain
    assert rec.status is AssetStatus.active   # default
    assert rec.source == "import"             # default
    assert rec.metadata == {"k": "v"}


def test_missing_required_field_rejected():
    with pytest.raises(ValidationError):
        AssetImport.model_validate({"id": "a1", "type": "domain"})  # no value


def test_invalid_type_rejected():
    with pytest.raises(ValidationError):
        AssetImport.model_validate({"id": "a1", "type": "frobnicate", "value": "x"})


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        AssetImport.model_validate({"id": "a1", "type": "domain", "value": "x", "status": "on-fire"})


def test_parent_and_covers_optional():
    rec = AssetImport.model_validate(
        {"id": "s1", "type": "subdomain", "value": "api.example.com", "parent": "d1"}
    )
    assert rec.parent == "d1"
    assert rec.covers is None
