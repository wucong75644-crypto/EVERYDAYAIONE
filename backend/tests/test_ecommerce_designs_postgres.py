"""v3 planning/assembly against real isolated transactions; model replies mocked."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_ecommerce_recovery_postgres import ecom_db, isolated_db, seed, scope, reserve
from tests.ecommerce_design_fixtures import design_fixture
from tests.test_ecommerce_workflow import planner


def save_draft(connection, facts, attempt, draft, credits=4):
    return connection.execute("SELECT finish_ecom_plan_draft_attempt(%s,%s,%s,%s,%s,%s,%s)",
        (facts["plan"], facts["lease"], facts["stage"], attempt, Jsonb({"user_credits": credits}), Jsonb(draft), credits)).fetchone()[0]


def test_draft_charge_and_receipt_are_atomic_idempotent_and_do_not_advance_stage(ecom_db):
    facts = seed(ecom_db, stage=3)
    draft = {"delivery_version": "ecom-design.v3", "output": {"images": [{"position": 1}]}, "issues": [{"path": ["images", 0, "lighting"], "code": "missing"}]}
    with psycopg.connect(ecom_db) as connection:
        scope(connection, facts["user"])
        attempt, _ = reserve(connection, facts)
        assert save_draft(connection, facts, attempt, draft)["outcome"] == "saved"
        assert save_draft(connection, facts, attempt, draft)["outcome"] == "replay"
        row = connection.execute("SELECT stage_drafts,stage_outputs,current_stage,status FROM ecom_image_plans WHERE id=%s", (facts["plan"],)).fetchone()
        assert row == ({"3": draft}, {}, 3, "planning")
        assert connection.execute("SELECT credits FROM users WHERE id=%s", (facts["user"],)).fetchone()[0] == 96
        assert connection.execute("SELECT count(*) FROM credits_history WHERE user_id=%s", (facts["user"],)).fetchone()[0] == 1
    with psycopg.connect(ecom_db) as connection:
        scope(connection, facts["user"])
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="ATTEMPT_CONFLICT"):
            save_draft(connection, facts, attempt, {**draft, "output": "another reply"})
    with psycopg.connect(ecom_db) as connection:
        scope(connection, facts["user"])
        attempt, _ = reserve(connection, facts)
        with pytest.raises(psycopg.errors.RaiseException, match="INSUFFICIENT_CREDITS"):
            save_draft(connection, facts, attempt, draft, credits=100)
    with psycopg.connect(ecom_db) as connection:
        assert connection.execute("SELECT credits FROM users WHERE id=%s", (facts["user"],)).fetchone()[0] == 96
        assert connection.execute("SELECT stage_drafts FROM ecom_image_plans WHERE id=%s", (facts["plan"],)).fetchone()[0] == {"3": draft}


def test_draft_rpc_keeps_rls_lease_and_invoker_permissions(ecom_db):
    facts = seed(ecom_db, stage=3)
    with psycopg.connect(ecom_db) as connection:
        scope(connection, facts["user"])
        attempt, _ = reserve(connection, facts)
    for user, lease in [(str(uuid4()), facts["lease"]), (facts["user"], str(uuid4()))]:
        with psycopg.connect(ecom_db) as connection:
            scope(connection, user)
            with pytest.raises(psycopg.Error):
                save_draft(connection, {**facts, "lease": lease}, attempt, {"output": "invalid"})
    with psycopg.connect(ecom_db) as connection:
        assert connection.execute("SELECT stage_drafts FROM ecom_image_plans WHERE id=%s", (facts["plan"],)).fetchone()[0] == {}
        assert connection.execute("SELECT prosecdef,has_function_privilege('image_untrusted',oid,'EXECUTE') FROM pg_proc WHERE proname='finish_ecom_plan_draft_attempt'").fetchone() == (False, False)


@pytest.mark.parametrize("mode,count,repair", [("main_images", 1, False), ("main_images", 15, True), ("detail_page", 5, False), ("detail_page", 15, True)])
async def test_real_planner_uses_page_settings_for_both_modes_and_preserves_image_tool_input(ecom_db, monkeypatch, mode, count, repair):
    from core.local_db import LocalDBClient
    from services.adapters.base import StreamChunk
    from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    facts = seed(ecom_db)
    final, evidence, public = design_fixture(count, mode, 3)
    raw = evidence["input_snapshot"]["messages"][0]["parts"][0]["text"]
    refs = [{key: value for key, value in ref.items() if key != "source_id"} for ref in public]
    resolved = [{**ref, "workspace_path": f"products/{index}.png", "content_sha256": str(index + 1) * 64,
        "file_version": 1, "width": 1024, "height": 1024, "aspect_ratio": "1:1"} for index, ref in enumerate(public)]
    workflow = WorkflowBinding(kind="main_images", root_task_id=facts["task"], window_task_id=facts["task"], generation_run_id=facts["run"])
    with psycopg.connect(ecom_db) as connection:
        connection.execute("UPDATE messages SET content=%s WHERE id=%s", (json.dumps([{"type": "text", "text": raw}]), facts["message"]))
        connection.execute("UPDATE tasks SET request_params=%s WHERE id=%s", (Jsonb({"_ecom_workflow": workflow.model_dump(mode="json")}), facts["task"]))
    db = LocalDBClient(psycopg.conninfo.make_conninfo(ecom_db, user="everydayai"), min_size=1, max_size=3)
    owner = SimpleNamespace(db=db, user_id=facts["user"], workspace_user_id=facts["user"], org_id=None,
        conversation_id=facts["conv"], task_id=facts["task"], context_scope="user", execution_mode="interactive",
        image_execution_token=facts["token"], _current_tool_call_id="plan", cancellation_event=asyncio.Event(),
        execution_budget=None, image_skill_snapshot=({"skill_key": "ecommerce-main-images"},))
    service = EcommerceImagePlanner(owner)
    service.settings = SimpleNamespace(**vars(planner().settings), ecom_image_planning_enabled=True)
    class Resolver:
        def __init__(self, *args, **kwargs):
            pass
        def _message(self, message_id):
            return {"role": "user", "content": [{"type": "text", "text": raw}]}
        def resolve(self, selected):
            assert selected == refs
            return deepcopy(resolved)
        def verify(self, selected):
            assert [ref["asset_id"] for ref in selected] == [ref["asset_id"] for ref in public]
        def preview(self, selected):
            return f"https://example.invalid/{public.index(next(ref for ref in public if ref['asset_id'] == selected['asset_id']))}.png"
        def size_context(self):
            return {}, {}
        def normalize_legacy(self, args):
            return args
    monkeypatch.setattr("services.handlers.chat_image_request.ChatImageInputResolver", Resolver)
    seen = []
    broken = deepcopy(final)
    if repair:
        del broken["images"][1]["lighting"]
        del broken["images"][12]["text_layout"]
    def open_chat(request):
        async def stream(sent, **kwargs):
            body = json.loads(sent[1]["content"][0]["text"])
            seen.append(body)
            stage = body["stage"]
            if stage == 3 and repair:
                if "repair_targets" in body:
                    reply = {"patches": [{"path": ["images", 1, "lighting"], "value": final["images"][1]["lighting"]},
                        {"path": ["images", 12, "text_layout"], "value": final["images"][12]["text_layout"]}], "review_records": final["review_records"]}
                else:
                    reply = broken
            else:
                reply = {1: evidence["product_selling_points"], 2: evidence["visual_direction"], 3: final}[stage]
            yield StreamChunk(content=reply if isinstance(reply, str) else json.dumps(reply), prompt_tokens=100, completion_tokens=10)
        return SimpleNamespace(stream_chat=stream, last_result=SimpleNamespace(status="completed", usage={}), close=AsyncMock())
    monkeypatch.setattr("services.agent.image.ecommerce_planner.service.get_model_gateway", lambda: SimpleNamespace(open_chat=open_chat))
    try:
        fixed = {"task_type": mode, "platform": "淘宝", "language": "中文（简体）", "aspect_ratio": "3:4", "resolution": "2K", "image_count": count}
        result = await service.run({"references": refs}, fixed_settings=fixed)
        assert result.status == "success", result.summary
        assert [body["stage"] for body in seen] == ([1, 2, 3, 3] if repair else [1, 2, 3])
        assert all(body["settings"]["task_type"] == mode and body["settings"]["image_count"] == count for body in seen)
        assert all(body["raw_user_texts"] == [{"source_ref": "text_1", "text": raw}] for body in seen)
        with psycopg.connect(ecom_db) as connection:
            row = connection.execute("SELECT items,input_snapshot,stage_drafts,status,stage_outputs FROM ecom_image_plans WHERE id=%s", (result.metadata["plan_id"],)).fetchone()
            items, snapshot, drafts, status, outputs = row
            assert status == "ready" and len(items) == count
            assert snapshot["source_bindings"]["image_1"]["source_id"] == public[0]["asset_id"]
            assert snapshot["target_size"]["aspect_ratio"] == "3:4" and snapshot["target_size"]["resolution"] == "2K"
            assert all(item["references"] == public and item["design"] == design for item, design in zip(items, final["images"]))
            assert bool(drafts) == repair
            assert outputs["3"]["page_plan"] == final["page_plan"]
            assert connection.execute("SELECT count(*) FROM credits_history WHERE user_id=%s", (facts["user"],)).fetchone()[0] == len(seen)
        # Use the existing Image bridge with the saved plan; the bridge must
        # submit the exact assembled text and frozen reference order/parameters.
        import services.handlers.image_handler as bridge
        monkeypatch.setattr("core.config.get_settings", lambda: SimpleNamespace(chat_image_async_enabled=True,
            chat_image_transparent_enabled=False, chat_image_max_requests=15, chat_image_max_credits=300))
        monkeypatch.setattr("services.tools.dispatcher.current_dispatch_call_id", lambda: "generate-1")
        owner._current_tool_call_id = "generate-1"
        owner.db = service.scope
        receipt = await bridge.ImageHandler(service.scope).accept_chat_image(owner, {"plan_source": {"plan_id": result.metadata["plan_id"], "revision": 1, "item_id": items[0]["item_id"]}})
        assert receipt["status"] == "submitted"
        with psycopg.connect(ecom_db) as connection:
            request = connection.execute("SELECT request_params FROM tasks WHERE id=%s", (receipt["task_id"],)).fetchone()[0]
            image_snapshot = request["_media_request_v1"]
            assert image_snapshot["prompt"] == items[0]["request_text"]
            assert image_snapshot["aspect_ratio"] == "3:4" and image_snapshot["resolution"] == "2K"
            assert [ref["asset_id"] for ref in image_snapshot["references"]] == [ref["asset_id"] for ref in public]
    finally:
        db.close()
