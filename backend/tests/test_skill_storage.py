"""Real filesystem checks, including symlink and hardlink workspace escapes."""

import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.skills.contracts import PackageCreate, PublishRevision, SkillError, revision_path
from services.skills.storage import MAX_SKILL_BYTES, SkillStorage


PACKAGE = PackageCreate(skill_key="report", source="platform", scope_kind="platform")
BODY = "\n# 报表\n只读取数据。\n"
DOCUMENT = "---\nskill_key: report\nrevision: v1\ndescription: 报表说明\n---\n" + BODY


def publication(document=DOCUMENT, body=BODY, **kwargs):
    values = dict(revision="v1", content_sha256=hashlib.sha256(document.encode()).hexdigest(),
                  body_sha256=hashlib.sha256(body.encode()).hexdigest())
    return PublishRevision(**(values | kwargs))


@pytest.fixture
def storage(tmp_path):
    root, workspace = tmp_path / "skills", tmp_path / "workspace"
    root.mkdir()
    workspace.mkdir()
    return SkillStorage(str(root), workspace_root=str(workspace))


def write_skill(storage, document=DOCUMENT):
    target = storage.root / revision_path(PACKAGE, "v1")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(document.encode())
    return target


def test_valid_document_and_exact_hashes(storage):
    write_skill(storage)
    result = storage.validate(PACKAGE, publication())
    assert result.body == BODY
    assert result.summary == "报表说明"
    assert result.nas_path == "platform/report/v1/SKILL.md"


def test_catalog_metadata_is_validated_at_publication(storage):
    document = DOCUMENT.replace("description: 报表说明\n", """description: 报表说明
catalog:
  name: 业务报表
  triggers: [汇总业务]
  model_selectable: true
  allowed_tool_names: [erp_query]
  required_permissions: [order.view]
""")
    write_skill(storage, document)
    metadata = storage.validate(PACKAGE, publication(document)).catalog_metadata
    assert metadata.name == "业务报表" and metadata.triggers == ("汇总业务",)
    assert metadata.model_selectable is True and metadata.allowed_tool_names == ("erp_query",)


@pytest.mark.parametrize("metadata", [
    "null", "[]", "{model_selectable: 'true'}", "{triggers: abc}",
    "{execution_modes: [arbitrary]}", "{agent_domains: [admin]}",
    "{body: secret}", "{nas_path: /secret}", "{allowed_tool_names: ['']}",
])
def test_invalid_catalog_metadata_is_rejected_without_echoing_content(storage, metadata):
    document = DOCUMENT.replace("description: 报表说明\n", f"description: 报表说明\ncatalog: {metadata}\n")
    write_skill(storage, document)
    with pytest.raises(SkillError) as error:
        storage.validate(PACKAGE, publication(document))
    assert str(error.value) == "SKILL_CATALOG_METADATA_INVALID"


def test_crlf_body_is_not_normalized(storage):
    document, body = DOCUMENT.replace("\n", "\r\n"), BODY.replace("\n", "\r\n")
    write_skill(storage, document)
    assert storage.validate(PACKAGE, publication(document, body)).body == body


@pytest.mark.parametrize("field,error", [("content_sha256", "CONTENT"), ("body_sha256", "BODY")])
def test_hash_mismatch(storage, field, error):
    write_skill(storage)
    with pytest.raises(SkillError, match=f"SKILL_{error}_HASH_MISMATCH"):
        storage.validate(PACKAGE, publication(**{field: "0" * 64}))


@pytest.mark.parametrize("path", ["../SKILL.md", "platform/../../SKILL.md", "/tmp/SKILL.md",
    "platform/../SKILL.md", "./SKILL.md", "platform//SKILL.md", "platform\\SKILL.md", "SKILL.md\x00"])
def test_path_traversal_rejected(storage, path):
    with pytest.raises(SkillError, match="SKILL_PATH_INVALID"):
        storage._read(path)


def test_wrong_namespace_rejected_before_read(storage):
    write_skill(storage)
    with pytest.raises(SkillError, match="SKILL_PATH_IDENTITY_MISMATCH"):
        storage.validate(PACKAGE, publication(), nas_path="org/foreign/report/v1/SKILL.md")


@pytest.mark.parametrize("kind", ["file_symlink", "directory_symlink", "hardlink", "inside_symlink"])
def test_workspace_links_cannot_be_published(storage, kind):
    target = write_skill(storage)
    external = storage.workspace_root / "SKILL.md"
    external.write_text(DOCUMENT)
    target.unlink()
    if kind == "file_symlink":
        target.symlink_to(external)
    elif kind == "directory_symlink":
        target.parent.rmdir()
        target.parent.symlink_to(storage.workspace_root, target_is_directory=True)
    elif kind == "hardlink":
        os.link(external, target)
    else:
        inside = storage.root / "SKILL.md"
        inside.write_text(DOCUMENT)
        target.symlink_to(inside)
    with pytest.raises(SkillError):
        storage.validate(PACKAGE, publication())


@pytest.mark.parametrize("location", ["same", "below", "above", "symlink", "missing", "relative", "unset"])
def test_bad_storage_configuration(tmp_path, location):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    child = workspace / "skills"
    child.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workspace, target_is_directory=True)
    root = {"same": workspace, "below": child, "above": tmp_path, "symlink": alias,
            "missing": tmp_path / "absent", "relative": Path("skills"), "unset": None}[location]
    with pytest.raises(SkillError):
        SkillStorage(str(root) if root else None, workspace_root=str(workspace))


@pytest.mark.parametrize("document,error", [
    (BODY, "FRONTMATTER_REQUIRED"),
    ("---\nskill_key: report", "FRONTMATTER_UNCLOSED"),
    ("---\n- array\n---\nbody", "FRONTMATTER_NOT_MAPPING"),
    (DOCUMENT.replace("skill_key: report", "skill_key: other"), "IDENTITY_MISMATCH"),
    (DOCUMENT.replace("revision: v1", "revision: v2"), "IDENTITY_MISMATCH"),
    (DOCUMENT.replace("revision: v1", "revision: 1"), "IDENTITY_MISMATCH"),
    (DOCUMENT.replace("skill_key: report", "skill_key: report\nskill_key: other"), "DUPLICATE"),
    (DOCUMENT.replace("description: 报表说明", "description: ["), "FRONTMATTER_INVALID"),
    (DOCUMENT.replace("description: 报表说明", "description: true"), "DESCRIPTION_INVALID"),
    (DOCUMENT.replace("description: 报表说明", "description: !!python/object/apply:os.system [id]"), "FRONTMATTER_INVALID"),
    (DOCUMENT.replace("description: 报表说明", "description: &x [*x]"), "ALIAS_FORBIDDEN"),
    (DOCUMENT.replace(BODY, ""), "BODY_EMPTY"),
])
def test_frontmatter_is_validated(storage, document, error):
    write_skill(storage, document)
    with pytest.raises(SkillError, match=error):
        storage.validate(PACKAGE, publication(document))


@pytest.mark.parametrize("field", [
    pytest.param("date: 2026-13-01", id="invalid-date"),
    pytest.param("number: " + "9" * 5000, id="oversized-integer"),
    pytest.param("extra: !!map [a]", id="invalid-mapping"),
])
def test_yaml_constructor_errors_use_skill_error(storage, field):
    document = DOCUMENT.replace("description: 报表说明", "description: 报表说明\n" + field)
    write_skill(storage, document)
    with pytest.raises(SkillError, match="^SKILL_FRONTMATTER_INVALID$"):
        storage.validate(PACKAGE, publication(document))


def test_tampered_published_file_is_rejected(storage):
    target = write_skill(storage)
    storage.validate(PACKAGE, publication())
    target.write_text(DOCUMENT + "unexpected")
    with pytest.raises(SkillError, match="CONTENT_HASH_MISMATCH"):
        storage.validate(PACKAGE, publication())


def test_missing_oversized_non_utf8_and_fifo_files(storage):
    with pytest.raises(SkillError, match="READ_REJECTED"):
        storage.validate(PACKAGE, publication())
    target = write_skill(storage, "x" * (MAX_SKILL_BYTES + 1))
    with pytest.raises(SkillError, match="TOO_LARGE"):
        storage.validate(PACKAGE, publication())
    target.write_bytes(b"\xff")
    with pytest.raises(SkillError, match="UTF8_INVALID"):
        storage.validate(PACKAGE, publication(content_sha256=hashlib.sha256(b"\xff").hexdigest()))
    target.unlink()
    os.mkfifo(target)
    with pytest.raises(SkillError, match="FILE_NOT_REGULAR"):
        storage.validate(PACKAGE, publication())


def test_directory_symlink_swap_after_realpath_is_rejected(storage, monkeypatch):
    target = write_skill(storage)
    (storage.workspace_root / "SKILL.md").write_text(DOCUMENT)
    original = Path.resolve

    def swap(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == target:
            target.unlink()
            target.parent.rmdir()
            target.parent.symlink_to(storage.workspace_root, target_is_directory=True)
        return result

    monkeypatch.setattr(Path, "resolve", swap)
    with pytest.raises(SkillError, match="READ_REJECTED"):
        storage.validate(PACKAGE, publication())


@pytest.mark.parametrize("values", [
    {"skill_key": "../escape"}, {"skill_key": "UPPER"}, {"source": " "},
    {"scope_kind": "org"}, {"org_id": "00000000-0000-0000-0000-000000000001"},
])
def test_package_contract_invalid(values):
    with pytest.raises(ValidationError):
        PackageCreate(**(PACKAGE.model_dump() | values))


@pytest.mark.parametrize("values", [{"revision": ".."}, {"revision": "x/y"}, {"content_sha256": "xyz"}])
def test_revision_contract_invalid(values):
    with pytest.raises(ValidationError):
        publication(**values)
