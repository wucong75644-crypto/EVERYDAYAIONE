"""A size request for the third draft must never alter its siblings."""
import json

import pytest
from pydantic import ValidationError
from jsonschema import Draft202012Validator

from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
from services.task_execution import content_groups, content_objects as co, saved_image_revision
from tests.test_content_objects import document, registered, ref
from tests.semantic_fixtures import semantic_request
from tests.test_task_execution_model_bridge import call


def rules(indices=(3,), **size):
    return [{'scope': 'items', 'item_indices': list(indices), 'size': size or {'aspect_ratio': '3:4', 'resolution': '2K'}}]


def test_third_only_and_whole_group_have_explicit_distinct_contracts():
    original = co.ContentDocument.model_validate(document())
    frame = ImageRequestContext.model_validate({'size_rules': rules()})
    result = content_groups.bind_group(original, [], '', semantic_context=frame)
    assert result.items[:2] == original.items[:2]
    assert result.items[2].options == {**original.items[2].options, 'aspect_ratio': '3:4', 'resolution': '2K'}
    whole = ImageRequestContext.model_validate({'size_rules': [{'scope': 'all', 'size': {'resolution': '4K'}}]})
    result = content_groups.bind_group(original, [], '', semantic_context=whole)
    assert all(i.options['resolution'] == '4K' and i.options['aspect_ratio'] == '1:1' for i in result.items)
    auto = ImageRequestContext.model_validate({'size_rules': rules(mode='auto')})
    result = content_groups.bind_group(original, [], '', semantic_context=auto)
    assert result.items[:2] == original.items[:2] and result.items[2].options['aspect_ratio'] == 'auto'
    Draft202012Validator(ImageRequestContext.model_json_schema()).validate(frame.model_dump(mode='json'))


@pytest.mark.parametrize('value', [
    {'size': {'resolution': '1K'}, 'size_rules': rules()},
    {'size_rules': rules() + rules(resolution='4K')},
    {'size_rules': [{'scope': 'items', 'item_indices': [], 'size': {'resolution': '2K'}}]},
    {'size_rules': [{'scope': 'all', 'item_indices': [3], 'size': {'resolution': '2K'}}]},
])
def test_ambiguous_or_conflicting_size_scopes_are_rejected(value):
    with pytest.raises(ValidationError): ImageRequestContext.model_validate(value)


@pytest.mark.asyncio
async def test_saved_revision_gate_compares_only_selected_item_sizes():
    runtime, prepared, handler, request, row = await registered()
    request = semantic_request(request, 'execute_saved', size_rules=rules())
    first = call('generate_image', {'content_source': ref(row, 1)}, 'first')
    third = call('generate_image', {'content_source': ref(row, 3)}, 'third')
    errors = saved_image_revision.check(runtime, request, [first, third], runtime.task_execution_state)
    assert set(errors) == {'third'}
    assert errors['third']['requested_size'] == {'aspect_ratio': '3:4', 'resolution': '2K'}
