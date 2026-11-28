"""Fixed storage version is host-owned; document identity/revision stays intact."""
import copy

import pytest
from jsonschema import Draft202012Validator

from services.handlers.chat import content_tools
from services.task_execution import content_objects as co
from tests.test_content_objects import document, registered, ref
from tests.test_reference_prompt_wire_contract import public_first_tool, refs_with_drafts


@pytest.mark.parametrize('reference', [False, True])
def test_public_authoring_schema_does_not_ask_model_for_fixed_protocol_version(reference):
    original = copy.deepcopy(co.SCHEMA)
    if reference:
        refs, _ = refs_with_drafts()
        function = public_first_tool(refs)
    else:
        function = content_tools.model_tools([original], first_delivery=True)[0]['function']
    doc = function['parameters']['properties']['document']
    assert 'version' not in doc['properties']
    assert 'version' not in doc['required']
    for value in function['parameters']['examples']:
        Draft202012Validator(function['parameters']).validate(value)
        assert 'version' not in value['document']
        assert all('version' not in item for item in value['document']['items'])
    assert original == co.SCHEMA  # No native schema or storage model was changed.


def test_examples_and_schema_drop_only_document_protocol_version():
    schema = co.save_parameters(require_presentation=False)
    schema['examples'] = [{'document': {**document(), 'version': 2}, 'continue_execution': False}]
    public = content_tools._authoring_schema(schema)
    assert 'version' not in public['examples'][0]['document']
    assert schema['examples'][0]['document']['version'] == 2
    assert co.SaveContent.model_validate(public['examples'][0]).document.version == 2
    assert public['examples'][0]['document']['items'] == schema['examples'][0]['document']['items']


@pytest.mark.asyncio
async def test_storage_and_history_keep_protocol_version_and_saved_revision():
    runtime, _, _, _, row = await registered()
    artifact, record, item = co._load_record(runtime.task_execution_state, ref(row, 3))
    assert record.version == record.document.version == 2
    assert record.revision == row['revision'] == 1
    assert artifact.id == row['artifact_id']
    assert item.body == document()['items'][2]['body']
