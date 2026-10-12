from uuid import uuid4
from pathlib import Path
import json
import pytest
from pydantic import ValidationError
from services.social_ops.contracts import (
    ProfileData,
    ResearchCreate,
    BudgetConfig,
    Produce,
)
from services.social_ops.repository import source_key, display_url
from services.social_ops.validation import verify_evidence, check_draft
from services.social_ops.skill_bundle import bundle_specs
from services.skills.authoring_contracts import reviewed_document
from services.skills.contracts import PackageCreate
from services.skills.renderer import (
    prepare_resources,
    render_resources,
    MAX_RENDERED_BYTES,
    MAX_BODY_BYTES,
)


def test_dedupe_ignores_expiring_token_and_display_redacts():
    a = "https://www.xiaohongshu.com/explore/abc123?xsec_token=secretA&xsec_source=pc_search"
    b = "https://www.xiaohongshu.com/discovery/item/abc123?xsec_token=secretB"
    assert source_key(a) == source_key(b) == "xiaohongshu:abc123"
    assert "secret" not in display_url(a)


def test_strict_configuration_and_platform_boundaries():
    with pytest.raises(ValidationError):
        ProfileData(name="n", positioning="p", audience="a", org_id=str(uuid4()))
    base = dict(client_request_id=uuid4(), connection_id=uuid4(), profile_id=uuid4())
    with pytest.raises(ValidationError):
        ResearchCreate(**base, comment_pages=4)
    with pytest.raises(ValidationError):
        ResearchCreate(**base, sample_urls=["https://example.com"])
    with pytest.raises(ValidationError):
        BudgetConfig(daily_limit=0, min_interval_seconds=0)
    with pytest.raises(Exception):
        BudgetConfig(
            daily_limit=20, min_interval_seconds=30, timezone="missing/timezone"
        )


def test_fabricated_evidence_rejected_missing_media_is_never_ready():
    source = {"id": str(uuid4()), "body": "真实原文片段"}
    verify_evidence(
        {"evidence": [{"snapshot_id": source["id"], "quote": "原文片段"}]}, [source]
    )
    with pytest.raises(ValueError):
        verify_evidence(
            {"evidence": [{"snapshot_id": source["id"], "quote": "编造内容"}]}, [source]
        )
    with pytest.raises(ValueError):
        verify_evidence(
            {"evidence": [{"snapshot_id": str(uuid4()), "quote": "原文片段"}]}, [source]
        )
    result = check_draft(
        {"title": "标题", "body": "正文", "evidence_ids": [source["id"]]}, [source]
    )
    assert result["status"] == "needs_media" and not result["ready_to_publish"]
    assert not result["hard_errors"]
    assert check_draft({"title": "长" * 21, "body": ""}, [])["hard_errors"]


def test_all_eight_bundles_fit_existing_asset_runtime_and_deny_tools():
    specs = bundle_specs()
    assert len(specs) == 8
    for spec in specs:
        assert spec.content.catalog_metadata.allowed_tool_names == ()
        assert spec.content.catalog_metadata.tool_policy == "restricted"
        package = PackageCreate(
            skill_key=spec.skill_key, source="easel", scope_kind="org", org_id=uuid4()
        )
        pub, raw = reviewed_document(package, "test-v1", spec.content)
        from services.skills.storage import SkillStorage

        validated = SkillStorage.validate_bytes(package, pub, raw)
        ids, base, values = prepare_resources(validated, {}, MAX_RENDERED_BYTES)
        rendered = render_resources(
            validated,
            ids,
            base,
            values,
            {a.id: a.content for a in spec.content.assets},
            MAX_RENDERED_BYTES,
        )
        assert rendered and len(spec.content.body.encode()) <= MAX_BODY_BYTES
    manifest = json.loads(
        (
            Path(__file__).parents[1] / "data/social_ops_skills/SOURCE_MANIFEST.json"
        ).read_text()
    )
    assert manifest["commit"] == "e6d5e8ebda73e1d9f9c4f7beb6e960979353ebec"
    assert all(p["dependencies"] for p in manifest["packages"])


@pytest.mark.parametrize(
    "action", ["create_post", "create_comment", "set_like", "upload_video"]
)
def test_scheduled_reach_reads_allowed_but_all_writes_denied(action):
    from services.tools import ToolPolicy, ToolContext, build_legacy_catalog

    ctx = ToolContext(
        actor_user_id="actor",
        workspace_owner_id="actor",
        org_id="org",
        context_scope="user",
        personal_context_allowed=True,
        agent_domain="general",
        permission_mode="auto",
        execution_mode="scheduled",
        task_id="research-task",
        authorized_tool_names=frozenset({"agent_reach"}),
        feature_flags={"agent_reach_enabled": True},
        authorization_snapshot={"version": 1, "allowed_tools": ["agent_reach"]},
    )
    policy = ToolPolicy(build_legacy_catalog())
    read = {
        "task": "research",
        "platform": "xiaohongshu",
        "action": "search",
        "query": "topic",
    }
    assert policy.decide("agent_reach", ctx, read).outcome == "allow"
    args = {
        "task": "write",
        "platform": "xiaohongshu",
        "action": action,
        "connection_id": str(uuid4()),
        "content": "body",
        "title": "title",
        "url": "https://www.xiaohongshu.com/explore/abc?xsec_token=test",
        "visibility": "private",
    }
    if action in {"create_post", "upload_video"}:
        args["media"] = [
            {
                "name": "test",
                "resource_ref": "fref1_test",
                "role": "image" if action == "create_post" else "video",
            }
        ]
    assert policy.decide("agent_reach", ctx, args).outcome == "deny"


async def test_production_uses_pinned_runtime_and_gateway_without_tools(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from core.config import Settings
    from core.db_scope import DatabaseScope, DatabaseAccessKind
    from tests.test_skill_runtime import Source, item
    from tests.test_scheduled_skill_snapshots import snapshot
    from services.social_ops.production import ProductionWorker
    from services.adapters.types import StreamChunk

    source = Source(
        [item(key="easel-xhs-note-creator", tools=(), execution_modes=("scheduled",))],
        body="Only use the pinned method.",
    )
    pins = snapshot(source)
    for pin in pins["skills"]:
        pin["allowed_tool_names"] = []
    settings = Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        jwt_secret_key="test-only",
        skill_catalog_enabled=True,
        skill_runtime_enabled=True,
    )
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "services.skills.scheduled.source_for_executor", lambda *args: source
    )
    sid = str(uuid4())
    output = {
        "title": "有据标题",
        "body": "有据正文",
        "tags": [],
        "evidence": [{"snapshot_id": sid, "quote": "原文"}],
        "claims": [],
        "assumptions": [],
        "required_materials": ["自有图"],
        "missing_inputs": ["图片"],
    }
    observed = []

    class Session:
        async def stream_chat(self, messages, **kwargs):
            observed.append((messages, kwargs))
            yield StreamChunk(
                content=json.dumps(output), prompt_tokens=50, completion_tokens=20
            )

        close = AsyncMock()

    session = Session()
    monkeypatch.setattr(
        "services.model_gateway.get_model_gateway",
        lambda: SimpleNamespace(open_chat=lambda req: session),
    )

    @asynccontextmanager
    async def credit_lock(*args, **kwargs):
        assert kwargs["amount"] == 5
        yield SimpleNamespace(set_actual=lambda value: None)

    monkeypatch.setattr(
        "services.credit_service.CreditService.credit_lock", credit_lock
    )
    scope = DatabaseScope(
        actor_user_id=str(uuid4()),
        org_id=str(uuid4()),
        access_kind=DatabaseAccessKind.RUNTIME,
    )
    worker = ProductionWorker(SimpleNamespace(pool=None), scope)
    run = {
        "id": uuid4(),
        "stage_index": 0,
        "stages": [pins],
        "kind": "draft",
        "allowance": 5,
        "lease_token": uuid4(),
        "outputs": [],
        "input": {"sources": [{"id": sid, "body": "这是真实原文"}]},
    }
    result, manifest = await worker.execute_stage(run)
    assert result == output and manifest["skill_snapshot"] == pins
    assert "Only use the pinned method." in str(observed[0][0])
    assert "tools" not in observed[0][1]
    source.discover.assert_not_awaited()
    source.session_bindings.assert_not_awaited()
    assert source.load.await_count == 1
    session.close.assert_awaited_once()
