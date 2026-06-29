"""Conflict-merge and idempotency unit tests (dedup core logic, no DB needed)."""
from crud import merge_metadata, merge_tags, relationship_id


def test_merge_metadata_new_keys_win_old_retained():
    old = {"issuer": "LE", "expires": "2025-01-02", "a": 1}
    new = {"expires": "2026-01-02", "b": 2}
    assert merge_metadata(old, new) == {"issuer": "LE", "expires": "2026-01-02", "a": 1, "b": 2}


def test_merge_metadata_empty_incoming_does_not_wipe():
    old = {"issuer": "LE"}
    assert merge_metadata(old, {}) == {"issuer": "LE"}
    assert merge_metadata(old, None) == {"issuer": "LE"}


def test_merge_metadata_handles_none_old():
    assert merge_metadata(None, {"x": 1}) == {"x": 1}


def test_merge_tags_union_dedup_stable_order():
    assert merge_tags(["prod"], ["prod", "production"]) == ["prod", "production"]
    assert merge_tags(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
    assert merge_tags(None, None) == []


def test_relationship_id_is_deterministic_and_distinct():
    a = relationship_id("org1", "s1", "d1", "child_of")
    b = relationship_id("org1", "s1", "d1", "child_of")
    c = relationship_id("org1", "s1", "d1", "covers")
    d = relationship_id("org2", "s1", "d1", "child_of")
    assert a == b            # same edge -> same id -> idempotent
    assert a != c and a != d  # different type/org -> different id
