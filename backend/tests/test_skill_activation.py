from pathlib import Path

import pytest

from services.agent.runtime.context import (
    SkillActivationError,
    build_skill_context,
    discover_skill_metadata,
    load_skill_content,
    render_active_skill_instructions,
    select_skill_metadata,
)


def _write_skill(root: Path, name: str, body: str) -> None:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")


def test_selector_prefers_explicit_name_and_limits_activation(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "report",
        "---\nname: report\ndescription: 生成销售报告\n---\nreport body\n",
    )
    _write_skill(
        tmp_path,
        "image",
        "---\nname: image\ndescription: 生成商品图片\n---\nimage body\n",
    )
    result = discover_skill_metadata(tmp_path)

    selected = select_skill_metadata(result.skills, "请使用 report skill 生成销售报告")

    assert [skill.name for skill in selected] == ["report"]


def test_loader_verifies_discovery_snapshot_and_renders_body(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "report",
        "---\nname: report\ndescription: 生成销售报告\n---\n执行步骤\n",
    )
    result = discover_skill_metadata(tmp_path)
    activated = load_skill_content(tmp_path, result.skills[0])

    rendered = render_active_skill_instructions([activated])

    assert rendered is not None
    assert "执行步骤" in rendered
    assert "name=\"report\"" in rendered

    (tmp_path / "report" / "SKILL.md").write_text("changed", encoding="utf-8")
    with pytest.raises(SkillActivationError, match="SKILL_CONTENT_CHANGED"):
        load_skill_content(tmp_path, result.skills[0])


def test_selector_and_renderer_have_safe_empty_results() -> None:
    assert select_skill_metadata([], "任意任务") == ()
    assert render_active_skill_instructions([]) is None


def test_build_skill_context_keeps_catalog_and_active_body_separate(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "Skills",
        "report",
        "---\nname: report\ndescription: 生成销售报告\n---\n执行报告步骤\n",
    )

    context = build_skill_context(tmp_path, "请使用 report skill")

    assert context.issue_count == 0
    assert "执行报告步骤" not in (context.catalog or "")
    assert "执行报告步骤" in (context.instructions or "")
