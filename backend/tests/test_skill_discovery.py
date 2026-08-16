from pathlib import Path

from services.agent.runtime.context import (
    discover_skill_metadata,
    discover_skill_metadata_from_roots,
    discover_workspace_skill_metadata,
    workspace_skill_root,
)


def _write_skill(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")


def test_discovery_returns_metadata_without_loading_skill_body(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "customer-analysis/SKILL.md",
        "---\n"
        "name: customer-analysis\n"
        "description: 分析客户数据并输出结论\n"
        "when_to_use: 用户要求分析客户数据时\n"
        "---\n"
        "这是不应该进入发现结果的完整指令。\n",
    )

    result = discover_skill_metadata(tmp_path)

    assert result.issues == ()
    assert len(result.skills) == 1
    skill = result.skills[0]
    assert skill.name == "customer-analysis"
    assert skill.description == "分析客户数据并输出结论"
    assert skill.when_to_use == "用户要求分析客户数据时"
    assert skill.relative_path == "customer-analysis/SKILL.md"
    assert "完整指令" not in repr(skill)
    assert len(skill.content_hash) == 64


def test_discovery_skips_invalid_skills_and_is_deterministic(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "valid/SKILL.md",
        "---\nname: valid\ndescription: 可用\n---\nbody\n",
    )
    _write_skill(
        tmp_path,
        "missing-description/SKILL.md",
        "---\nname: missing-description\n---\nbody\n",
    )
    _write_skill(tmp_path, "no-frontmatter/SKILL.md", "body\n")

    first = discover_skill_metadata(tmp_path)
    second = discover_skill_metadata(tmp_path)

    assert first == second
    assert [skill.name for skill in first.skills] == ["valid"]
    assert [issue.code for issue in first.issues] == [
        "DESCRIPTION_INVALID",
        "FRONTMATTER_MISSING",
    ]


def test_discovery_does_not_follow_symlinked_skill(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_skill(outside, "external/SKILL.md", "---\nname: external\ndescription: 外部\n---\n")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    result = discover_skill_metadata(root)

    assert result.skills == ()
    assert result.issues == ()


def test_discovery_keeps_candidates_from_multiple_sources(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    personal = tmp_path / "personal"
    _write_skill(bundled, "report/SKILL.md", "---\nname: report\ndescription: 内置\n---\n")
    _write_skill(personal, "report/SKILL.md", "---\nname: report\ndescription: 个人\n---\n")

    result = discover_skill_metadata_from_roots([
        (bundled, "platform_bundled"),
        (personal, "workspace"),
    ])

    assert [(skill.name, skill.source) for skill in result.skills] == [
        ("report", "platform_bundled"),
        ("report", "workspace"),
    ]


def test_workspace_discovery_uses_user_visible_skills_directory(
    tmp_path: Path,
) -> None:
    skill_root = workspace_skill_root(tmp_path)
    _write_skill(
        skill_root,
        "personal/SKILL.md",
        "---\nname: personal\ndescription: 个人技能\n---\n",
    )

    result = discover_workspace_skill_metadata(tmp_path)

    assert skill_root == tmp_path / "Skills"
    assert [skill.relative_path for skill in result.skills] == [
        "personal/SKILL.md",
    ]
