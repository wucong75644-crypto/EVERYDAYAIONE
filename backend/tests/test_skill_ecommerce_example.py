"""The full merged Skill is a real, activatable package, including its schema."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.skills.authoring_contracts import CreateSkill, reviewed_document
from services.skills.contracts import PackageCreate
from services.skills.renderer import prepare_resources, render_resources
from services.skills.runtime import skill_budget_options
from services.skills.storage import SkillStorage


@pytest.mark.parametrize("version,payload", [("v1", "ecommerce-image-prompts.create.json"),
    ("v2", "ecommerce-image-prompts.v2.create.json")])
def test_full_merged_package_is_publishable_and_rendered_without_truncation(tmp_path, version, payload):
    root = Path(__file__).resolve().parents[2] / 'examples/skills'
    request = CreateSkill.model_validate_json((root / payload).read_text())
    package = PackageCreate(skill_key=request.skill_key, source='user-merged', scope_kind='platform')
    publication, raw = reviewed_document(package, version, request.content)
    assert raw == (root / f"catalog/platform/ecommerce-image-prompts/{version}/SKILL.md").read_bytes()
    if version == "v1":
        normalized = request.content.body.replace('(附件 product-analysis-schema)',
            '(references/product-analysis.schema.json)').removesuffix(
            '\n\n商品分析协议原文（平台附件，保持字段与枚举原样）：[[asset:product-analysis-schema]]\n')
        assert len(normalized.encode()) == 128516
        assert hashlib.sha256(normalized.encode()).hexdigest() == '681a1b12b78f8e05dd15d4050abbee57b2ae0b030dd8252dcfc3b0ededb39aaf'
    storage = SkillStorage(str(root / 'catalog'), workspace_root=str(tmp_path / 'workspace'))
    skill = storage.validate(package, publication)
    options = skill_budget_options(SimpleNamespace())
    ids, base, values = prepare_resources(skill, {}, options['maximum_rendered'],
        maximum_body=options['maximum_body'], maximum_rendered=options['maximum_rendered'])
    texts = storage.read_assets(skill, ids)
    rendered = render_resources(skill, ids, base, values, texts, options['maximum_rendered'])
    assert skill.body in rendered
    assert request.content.assets[0].content in rendered
    assert len(rendered.encode()) > 128516
    json.loads(texts['product-analysis-schema'])
    assert skill.catalog_metadata.model_selectable is False
    assert skill.catalog_metadata.tool_policy == 'platform'
