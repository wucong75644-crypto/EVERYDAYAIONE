"""Runtime v3 Skill context snapshot and progressive loading tests."""

from pathlib import Path
from types import SimpleNamespace

from services.agent.runtime.context import (
    SkillActivationError,
    SkillContext,
    SkillSnapshot,
    restore_skill_context,
    skill_context_snapshot,
)
from services.agent.runtime.ports.coordinator_recovery import (
    RunAggregateSnapshot,
)
from services.agent.runtime.production_model import _runtime_messages


def _snapshot_with_skill(skill_context: SkillContext) -> RunAggregateSnapshot:
    return RunAggregateSnapshot(
        run={},
        latest_model_step={
            "request_receipt": {
                "skill_context": skill_context_snapshot(skill_context),
            },
        },
        unresolved_model_attempt=None,
        latest_model_result=None,
        model_steps=(),
        actions=(),
    )


def _skill_context() -> SkillContext:
    return SkillContext(
        catalog=(
            "<available_skills><skill><name>report</name>"
            "<path>Skills/report/SKILL.md</path></skill></available_skills>"
        ),
        instructions=(
            "<active_skills><skill_instructions name=\"report\">"
            "先分析再输出报告</skill_instructions></active_skills>"
        ),
        issue_count=0,
        active_skills=(SkillSnapshot(
            name="report",
            relative_path="report/SKILL.md",
            source="workspace",
            content_hash="a" * 64,
        ),),
    )


def test_skill_context_snapshot_round_trips_and_is_hash_bound() -> None:
    original = _skill_context()
    restored = restore_skill_context(skill_context_snapshot(original))

    assert restored == original


def test_skill_context_snapshot_rejects_tampering() -> None:
    from pytest import raises

    snapshot = skill_context_snapshot(_skill_context())
    snapshot["instructions"] = "tampered"

    with raises(SkillActivationError, match="SKILL_CONTEXT_RECEIPT_HASH_INVALID"):
        restore_skill_context(snapshot)


def test_runtime_discovers_workspace_skill_on_first_model_step(tmp_path, monkeypatch):
    from core.workspace import resolve_workspace_dir

    monkeypatch.setattr(
        "core.config.get_settings",
        lambda: SimpleNamespace(file_workspace_root=str(tmp_path)),
    )
    workspace = resolve_workspace_dir(str(tmp_path), "user-1", None)
    skill_file = Path(workspace) / "Skills" / "report" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: report\ndescription: 生成销售报告\n---\n"
        "先分析再输出销售报告\n",
        encoding="utf-8",
    )

    messages, _, context = _runtime_messages(
        snapshot=RunAggregateSnapshot(
            run={}, latest_model_step=None, unresolved_model_attempt=None,
            latest_model_result=None, model_steps=(), actions=(),
        ),
        context={
            "session": {"user_id": "user-1", "org_id": None},
            "messages": [{
                "id": "m1", "role": "user", "content": "请使用 report 生成销售报告",
            }],
        },
        definition=SimpleNamespace(system_prompt="Runtime base"),
        payload={"params": {}}, model_id="qwen3.5-plus",
        input_message_id="m1",
    )

    assert Path(workspace).is_dir()
    assert "<name>report</name>" in (context.catalog or "")
    assert "先分析再输出销售报告" in (context.instructions or "")
    assert context.instructions in messages[0]["content"]


def test_runtime_reuses_persisted_skill_context_for_later_model_step() -> None:
    context = _skill_context()
    messages, _, restored = _runtime_messages(
        snapshot=_snapshot_with_skill(context),
        context={"messages": [{"role": "user", "content": "继续"}]},
        definition=SimpleNamespace(system_prompt="Runtime base"),
        payload={"params": {"permission_mode": "auto"}},
        model_id="qwen3.5-plus",
        input_message_id=None,
    )

    assert restored == context
    assert context.catalog in messages[0]["content"]
    assert context.instructions in messages[0]["content"]
    assert skill_context_snapshot(restored)["snapshot_hash"]
