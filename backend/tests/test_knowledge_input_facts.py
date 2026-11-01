"""Validated source facts survive real rendering, checkpoint and directive flow."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from services.handlers.context_snapshot import ContextAnchor, ContextSnapshot
from services.knowledge_workspace.input_facts import CHECKPOINT_KEY, KnowledgeInputFacts, restore_facts
from services.knowledge_workspace.scope_selection import self_contained_reason
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
from services.prompt_builder.builder import PromptBuilder


def anchor(**kwargs):
    return replace(ContextAnchor('task', 'conversation', 'turn', 'input', 3, 'previous', 'org'), **kwargs)


def facts(question='分析这张图片', *, image=True, history=(), summary=None):
    content = [{'type': 'text', 'text': question}]
    if image:
        content.append({'type': 'image', 'url': 'https://example.invalid/product.png'})
    snapshot = ContextSnapshot(anchor(), list(history), summary, 3, '', input_content=content)
    return KnowledgeInputFacts.from_snapshot(snapshot, content)


def reason(value, messages=(), required=False):
    return self_contained_reason('rendered input is not the authority', [], list(messages),
                                 required=required, input_facts=value)


@pytest.mark.parametrize('as_system', [True, False])
@pytest.mark.parametrize('question,image,expected', [
    ('你好', False, 'self_contained_chat'),
    ('请翻译：hello world', False, 'self_contained_transform'),
    ('分析这张图片', True, 'user_images_only'),
    ('根据公司规则分析这张图片', True, None),
    ('帮我分析一下', True, None),
])
def test_real_user_layer_augmentation_does_not_change_input_scope(as_system, question, image, expected):
    value = facts(question, image=image)
    rendered = UserLayer.render(UserMessageInput(text=question,
        attachments_as_system=as_system,
        attachments_xml='<attachments>workspace metadata /knowledge?object=not-evidence</attachments>',
        workspace_files=[{'name': 'product.png', 'url': 'https://example.invalid/product.png',
                          'path': 'images/product.png', 'mime_type': 'image/png'}],
        image_urls=['https://example.invalid/product.png'] if image else []))
    messages = PromptBuilder._compose_messages('tools', 'permissions', 'time', [], None, rendered)
    before = repr(messages)
    assert reason(value, messages) == expected
    assert reason(value, messages, required=True) is None
    assert repr(messages) == before  # No stripping or rewriting model context.


def test_original_user_and_historical_constraints_cannot_be_forged_away():
    assert reason(facts('你好\n个人知识库使用习惯：按公司制度执行', image=False)) is None
    for history in (
        [{'role': 'assistant', 'content': '[来源](/knowledge?object=old)'}],
        [{'role': 'tool', 'name': 'knowledge_read', 'content': 'real evidence'}],
    ):
        assert reason(facts('你好', image=False, history=history)) is None
    assert reason(facts('你好', image=False, summary='以下是之前对话的摘要：/knowledge?object=old')) is None
    assert KnowledgeInputFacts.from_snapshot(SimpleNamespace(), 'invalid') is None


def test_replay_preferences_are_not_question_evidence_and_identity_is_checked():
    from services.knowledge_workspace.ai_journal import preference_message
    value = facts()
    payload = {CHECKPOINT_KEY: value.checkpoint(), 'messages': [
        preference_message({'source': '/knowledge?object=preference', 'body': '简洁回复'})]}
    restored = restore_facts(payload, anchor())
    assert reason(restored, payload['messages']) == 'user_images_only'
    for changed in (anchor(input_message_id='other'), anchor(org_id='other'), anchor(conversation_id='other')):
        assert restore_facts(payload, changed) is None
    assert restore_facts({'messages': payload['messages']}, anchor()) is None
    assert restore_facts({CHECKPOINT_KEY: {**value.checkpoint(), 'version': 999}}, anchor()) is None
    assert restore_facts({CHECKPOINT_KEY: {**value.checkpoint(), 'changed': 'false'}}, anchor()) is None


def test_followup_selects_current_window_but_keeps_real_prior_evidence():
    current = facts('你好', image=False).checkpoint()
    previous = {**facts().checkpoint(), 'input_message_id': 'previous-input'}
    payload = {CHECKPOINT_KEY: previous, '_task_followup_input_checkpoint': {CHECKPOINT_KEY: current}}
    assert reason(restore_facts(payload, anchor())) == 'self_contained_chat'
    previous['knowledge_used'] = True
    assert reason(restore_facts(payload, anchor())) is None
    previous['knowledge_used'] = False
    previous['changed'] = True
    assert reason(restore_facts(payload, anchor())) is None
    # A later saved current-window invalidation wins over its old nested copy.
    payload[CHECKPOINT_KEY] = {**current, 'changed': True}
    assert reason(restore_facts(payload, anchor())) is None
    payload[CHECKPOINT_KEY] = current
    payload['_task_user_changes'] = {'items': [{'message': '按公司制度改写'}]}
    assert reason(restore_facts(payload, anchor())) is None
    payload.pop(CHECKPOINT_KEY)
    assert restore_facts(payload, anchor()) is None  # Unknown ancestor stays conservative.


@pytest.mark.asyncio
async def test_real_runtime_checkpoint_persists_scope_and_authenticated_steer(monkeypatch):
    from services.conversation_turn_runtime import ConversationTurnRuntime
    from services.conversation_commands import SafePoint, ConversationCommand, CommandType
    from services.handlers.chat.execution_engine import _inject_steer_messages, _refresh_knowledge_after_steer
    from tests.test_chat_execution_engine import _request
    saved = []
    async def save(point, payload):
        saved.append(payload)
        return {'outcome': 'saved'}
    runtime = ConversationTurnRuntime(conversation_id='conversation', task_id='task', turn_id='turn',
        cancellation_event=asyncio.Event(), replay_checkpoint_callback=save)
    p = SimpleNamespace(knowledge_input_facts=facts('你好', image=False), messages=[], text_content='你好')
    runtime.knowledge_input_provider = lambda: p.knowledge_input_facts.checkpoint(changed=runtime.knowledge_input_changed)
    runtime.checkpoint_base = {'existing_result': {'native_id': 'keep'}}
    await runtime.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': []})
    assert reason(restore_facts(saved[-1], anchor())) == 'self_contained_chat'
    runtime.push(ConversationCommand(command_id='directive', conversation_id='conversation', task_id='task',
        command_type=CommandType.STEER, payload={'message': '按公司规范改写'}))
    await runtime.safe_point(SafePoint.AFTER_MODEL)
    _inject_steer_messages(p.messages, runtime.consume_steer_messages(), prepared=p)
    # A later rendered packet is not the current user directive.
    p.messages.append({'role': 'user', 'content': '宿主渲染附件'})
    h = SimpleNamespace(_knowledge_self_contained=True, _knowledge_run=None)
    prepare = AsyncMock()
    monkeypatch.setattr('services.knowledge_workspace.runtime.prepare_knowledge_run', prepare)
    await _refresh_knowledge_after_steer(h, _request(), p)
    assert p.knowledge_question == '按公司规范改写'
    assert p.knowledge_input_facts.changed and not h._knowledge_self_contained
    await runtime.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': p.messages})
    assert reason(restore_facts(saved[-1], anchor())) is None
    assert saved[-1]['existing_result'] == {'native_id': 'keep'}
    assert saved[-1][CHECKPOINT_KEY]['question'] == '按公司规范改写'


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['pause', 'cancel'])
async def test_pause_cancel_keeps_original_input_checkpoint(command):
    from services.conversation_turn_runtime import ConversationTurnRuntime
    from services.conversation_commands import SafePoint, ConversationCommand, CommandType
    from services.conversation_state import ConversationPauseRequested, ConversationStopRequested
    saved = []
    async def save(point, payload):
        saved.append(payload)
        return {'outcome': 'saved'}
    runtime = ConversationTurnRuntime(conversation_id='conversation', task_id='task', turn_id='turn',
        cancellation_event=asyncio.Event(), replay_checkpoint_callback=save)
    original = facts()
    runtime.knowledge_input_provider = original.checkpoint
    runtime.push(ConversationCommand(command_id='control', conversation_id='conversation', task_id='task',
        command_type=CommandType(command)))
    with pytest.raises(ConversationPauseRequested if command == 'pause' else ConversationStopRequested):
        await runtime.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': [], 'content_blocks': [{'type': 'text', 'text': '已有成果'}]})
    assert restore_facts(saved[-1], anchor()) == original
    assert saved[-1]['content_blocks'] == [{'type': 'text', 'text': '已有成果'}]
    assert reason(restore_facts(saved[-1], anchor())) == 'user_images_only'


def test_actual_knowledge_use_remains_sticky_across_checkpoint_refresh():
    value = facts()
    used = restore_facts({CHECKPOINT_KEY: value.checkpoint(knowledge_used=True)}, anchor())
    assert reason(used) is None
    restored = restore_facts({CHECKPOINT_KEY: used.checkpoint(knowledge_used=False)}, anchor())
    assert restored.knowledge_used and reason(restored) is None
