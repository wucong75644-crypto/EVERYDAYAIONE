"""Tests for trace bundle validation and fixture inventory."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tests.agent_runtime import trace_schema
from tests.agent_runtime.trace_schema import (
    load_trace_manifest,
    validate_trace_bundle,
)


def test_manifest_matches_disk_and_every_fixture_validates() -> None:
    bundles = load_trace_manifest()

    assert len(bundles) == 18
    assert len({bundle["scenario"] for bundle in bundles}) == 18


def test_schema_rejects_missing_required_field() -> None:
    bundle = deepcopy(load_trace_manifest()[0])
    bundle.pop("command")

    with pytest.raises(
        ValueError,
        match="TRACE_BUNDLE_TOP_LEVEL_FIELDS_INVALID",
    ):
        validate_trace_bundle(bundle)


def test_schema_rejects_sensitive_fields_at_any_depth() -> None:
    bundle = deepcopy(load_trace_manifest()[0])
    bundle["references"]["artifacts"].append({
        "artifact_id": "synthetic",
        "content_hash": "sha256:synthetic",
        "access_token": "not-allowed",
    })

    with pytest.raises(ValueError, match="TRACE_SENSITIVE_FIELD:access_token"):
        validate_trace_bundle(bundle)


def test_schema_rejects_incomplete_projection_record() -> None:
    bundle = deepcopy(load_trace_manifest()[0])
    bundle["projection_records"][0].pop("sequence")

    with pytest.raises(ValueError, match="PROJECTION_RECORD_FIELDS_INVALID"):
        validate_trace_bundle(bundle)


def test_schema_rejects_incomplete_trace_step() -> None:
    bundle = deepcopy(load_trace_manifest()[0])
    bundle["trace"][1].pop("fencing_revision")

    with pytest.raises(ValueError, match="TRACE_STEP_FIELDS_INVALID:transition"):
        validate_trace_bundle(bundle)


def test_actorless_system_scope_is_valid() -> None:
    bundle = next(
        item for item in load_trace_manifest()
        if item["scenario"] == "actorless system scope"
    )

    validate_trace_bundle(bundle)
    assert bundle["session_scope"]["user_id"] is None


@pytest.mark.parametrize(
    ("scope_kind", "field", "value", "error"),
    [
        ("user", "user_id", None, "TRACE_USER_SCOPE_USER_REQUIRED"),
        ("user", "user_id", " ", "TRACE_SCOPE_IDENTITY_INVALID:user_id"),
        ("channel", "org_id", None, "TRACE_CHANNEL_SCOPE_ORG_REQUIRED"),
        ("channel", "org_id", " ", "TRACE_SCOPE_IDENTITY_INVALID:org_id"),
        ("system", "user_id", " ", "TRACE_SCOPE_IDENTITY_INVALID:user_id"),
        ("system", "org_id", " ", "TRACE_SCOPE_IDENTITY_INVALID:org_id"),
        ("system", "scope_id", " ", "TRACE_NONBLANK_TEXT_REQUIRED:scope_id"),
    ],
)
def test_schema_rejects_missing_or_blank_scope_identity(
    scope_kind,
    field,
    value,
    error,
) -> None:
    bundle = deepcopy(load_trace_manifest()[0])
    bundle["session_scope"]["scope_kind"] = scope_kind
    bundle["session_scope"][field] = value

    with pytest.raises(ValueError, match=error):
        validate_trace_bundle(bundle)


def test_manifest_rejects_silent_fixture_loss(
    tmp_path,
    monkeypatch,
) -> None:
    fixture = deepcopy(load_trace_manifest()[0])
    (tmp_path / "only.json").write_text(
        json.dumps(fixture),
        encoding="utf-8",
    )
    (tmp_path / "extra.json").write_text(
        json.dumps(fixture),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "fixtures": ["only.json"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(trace_schema, "FIXTURE_DIR", tmp_path)

    with pytest.raises(ValueError, match="TRACE_MANIFEST_SET_MISMATCH"):
        load_trace_manifest()
