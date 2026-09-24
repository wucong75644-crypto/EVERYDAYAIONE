"""Asset IO participates in Turn budgets, cancellation, and pinned replay."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.skills.renderer import MAX_RENDERED_BYTES, digest
from services.skills.runtime import SkillReplayError
from tests.test_skill_assets import attachment, document, publish
from tests.test_skill_runtime import Source, state, item, activate
from tests.test_skill_storage import storage  # noqa: F401


def source_for(storage, skill, count=1):
    candidates = [item(f'skill{i}') for i in range(count)]
    async def load(c, **kwargs):
        return replace(skill, skill_key=c.skill_key, catalog_metadata=c.catalog_metadata)
    async def assets(c, validated, ids):
        return storage.read_assets(validated, ids)
    return SimpleNamespace(discover=AsyncMock(return_value=candidates), load=AsyncMock(side_effect=load),
                           load_assets=AsyncMock(side_effect=assets), session_bindings=AsyncMock(return_value=[]))


async def test_turn_asset_budget_rejects_before_reading_or_mutating_state(storage):
    skill = publish(storage, document(attachment(content='x' * 17000)))
    source = source_for(storage, skill, 3)
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate('skill0')))['ok']
    assert (await runtime.activate(activate('skill1')))['ok']
    checkpoint = runtime.checkpoint()
    assert (await runtime.activate(activate('skill2')))['code'] == 'SKILL_ASSET_BUDGET_EXCEEDED'
    assert source.load_assets.await_count == 2
    assert runtime.checkpoint() == checkpoint


async def test_cancellation_during_asset_read_does_not_activate(storage):
    source = source_for(storage, publish(storage))
    runtime = state(source)
    await runtime.initialize()
    async def cancelled(*args):
        runtime.cancellation_event.set()
        return {'guide': 'A private reference.'}
    source.load_assets.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError):
        await runtime.activate(activate('skill0'))
    assert not runtime.active


async def test_duplicate_activation_and_compression_keep_one_exact_asset_copy(storage):
    source = source_for(storage, publish(storage))
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate('skill0')))['ok']
    for _ in range(3):
        assert (await runtime.activate(activate('skill0')))['code'] == 'SKILL_ALREADY_ACTIVE'
    assert source.load_assets.await_count == 1
    messages = []
    runtime.ensure_messages(messages)
    runtime.ensure_messages(messages)
    assert sum(message['content'].count('A private reference.') for message in messages) == 1
    resumed = state(source)
    await resumed.initialize(runtime.checkpoint())
    assert source.load_assets.await_count == 2
    assert resumed.messages() == runtime.messages()


async def test_oversize_replay_stops_before_asset_io(storage):
    source = source_for(storage, publish(storage))
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate('skill0')))['ok']
    checkpoint = runtime.checkpoint()
    checkpoint['active'][0]['rendered'] = 'x' * (MAX_RENDERED_BYTES + 1)
    checkpoint['active'][0]['rendered_sha256'] = digest(checkpoint['active'][0]['rendered'])
    source.load_assets.reset_mock()
    with pytest.raises(SkillReplayError, match='REPLAY_RENDER_INVALID'):
        await state(source).initialize(checkpoint)
    source.load_assets.assert_not_awaited()


async def test_legacy_plain_body_checkpoint_remains_restorable():
    source = Source(body='Existing no-asset skill.')
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate()))['ok']
    checkpoint = runtime.checkpoint()
    del checkpoint['active'][0]['asset_manifest_sha256']
    del checkpoint['active'][0]['loaded_asset_ids']
    resumed = state(source)
    await resumed.initialize(checkpoint)
    assert resumed.messages() == runtime.messages()
