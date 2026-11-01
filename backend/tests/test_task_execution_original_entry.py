"""Actual chat entry/loop with original pinned bodies; no provider or business IO.

Scripted model proposals exercise host routing/recovery, not natural-language
understanding. Full planning, phase handoff and saved-image reuse are covered by
test_task_execution_saved_image_reuse with the same RepositoryOriginalSource.
"""
import copy
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from schemas.message import TextPart
from services.handlers.chat.execution_engine import execute_chat
from services.skills.selection import SkillSelection
from tests.test_chat_execution_engine import _request
from tests.test_skill_runtime_actor import actor, prepared, handler
from tests.test_task_execution_bootstrap import bootstrap
from tests.test_task_execution_model_bridge import call
from tests.test_task_execution_saved_image_reuse import original_source


class LoopObserved(Exception):
    """Stop at a verified boundary without claiming the user task completed."""


SCENARIOS = [
    ('帮我生成五张详情页的图我所在的平台是拼多多平台 生成的背景需要适配产品的背景图 '
     '需要放在桌子上背景颜色不能太突兀 需要凸显产品一点', 'image', 5, None, 'plan'),
    ('只要五条主图提示词，不要生成图片', 'text', 1, 5, 'plan'),
    ('先完整规划五条提示词，再生成五张详情图', 'image', 5, None, 'plan'),
    ('按刚才已确认的完整方案生成五张主图，不要重新规划', 'image', 5, None, 'execute_ready'),
    ('直接执行已保存的最终提示词生成一张详情图，不要重写提示词', 'image', 1, None, 'execute_prompt'),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [None, 'reference-image-prompts', 'ecommerce-image-prompts'])
@pytest.mark.parametrize('goal,kind,minimum,item_count,action', SCENARIOS)
async def test_original_entry_reaches_model_and_recovers_bad_arguments(
        monkeypatch, key, goal, kind, minimum, item_count, action, original_source):
    from core.config import get_settings
    settings = get_settings().model_copy(update={
        'skill_catalog_enabled': True, 'skill_runtime_enabled': True,
        'skill_recommendations_enabled': False, 'ecom_image_planning_enabled': True,
    })
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    source = original_source
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource', lambda *_: source)
    # Any return to the old keyword workflow fails the regression immediately.
    legacy = AsyncMock(side_effect=AssertionError('legacy keyword workflow entered'))
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.EcommerceWorkflow.initialize', legacy)
    boot, state, anchor, _ = bootstrap()
    boot = replace(boot, original_goal=goal, require_coverage_review=True)
    runtime = actor()
    runtime.task_bootstrap = boot
    history = [{'role': 'system', 'content': '保留现有上下文'},
               {'role': 'assistant', 'content': '已有成果记录'},
               {'role': 'user', 'content': goal}]
    p = prepared()
    p.messages = copy.deepcopy(history)
    p.execution_context = replace(p.execution_context, org_id=None,
        actor_user_id=str(state.actor_user_id), workspace_owner_id=str(state.actor_user_id),
        feature_flags={**p.execution_context.feature_flags, 'ecom_image_planning_enabled': True,
                       'chat_image_async_enabled': True})
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream', AsyncMock(return_value=p))
    h = handler()
    h.org_id = None
    h.db = object()  # Legacy workflow condition must be reachable, not bypassed by missing DB.
    h._execute_tool_calls.side_effect = AssertionError('unexpected external business dispatch')
    selection = SkillSelection(skill_id=key, revision=source.values[key].revision) if key else None
    request = replace(_request(), content=[TextPart(text=goal)], selected_skill=selection,
        task_id=str(state.root_task_id), conversation_id=str(state.conversation_id),
        user_id=str(state.actor_user_id), context_anchor=anchor)
    delivery = {'id': 'delivery', 'description': goal, 'artifact_kind': kind,
                'minimum': minimum, 'item_count': item_count, 'evidence': [goal]}
    proposal = {'action': action, 'applicable_method_ids': [key] if key and action == 'plan' else [],
                'requirements': [delivery]}
    main_rounds = []
    review_rounds = []

    async def model(current, tools, *args, **kwargs):
        if kwargs.get('task_review'):
            review_rounds.append(kwargs['model_round'])
            assert tools == []
            return json.dumps({'action': action, 'deliveries': [
                {k: delivery[k] for k in ('artifact_kind', 'minimum', 'item_count', 'evidence')}
            ], 'uncertainty': []}), '', [], set()
        main_rounds.append(kwargs['model_round'])
        names = {t['function']['name'] for t in tools}
        if len(main_rounds) == 1:
            assert 'start_task_execution' in names
            assert [m for m in current.messages if m in history] == history
            if key:
                active = runtime.skill_runtime.active
                assert key in active
                assert active[key].body_sha256 == source.values[key].body_sha256
            else:
                assert not runtime.skill_runtime.active
                # Original manual-only methods must not be silently made auto-selectable.
                assert all(not c.catalog_metadata.model_selectable for c in source.candidates)
            return '', '', [call('start_task_execution', {}, 'invalid-start')], set()
        if len(main_rounds) == 2:
            assert runtime.task_execution_state is None
            error = next(m for m in current.messages if m.get('role') == 'tool'
                         and m.get('tool_call_id') == 'invalid-start')
            assert 'TASK_CONTROL_INPUT_INVALID' in error['content']
            return '', '', [call('start_task_execution', proposal, 'corrected-start')], set()
        final = runtime.task_execution_state
        assert final is not None and final.original_goal == goal
        assert any(r.artifact_kind == kind and r.minimum == minimum for r in final.requirements)
        assert final.source_message_ids == state.source_message_ids
        if action == 'plan' and key:
            assert {b.skill_key for b in final.method_bindings} == {key}
            assert 'generate_image' not in names  # Full method plan is still required.
        if action != 'plan':
            assert not final.method_bindings  # Loaded method is not a request to repeat planning.
            assert any(r.id.endswith('host.image_source') for r in final.requirements)
        assert not final.operations
        assert [m for m in current.messages if m in history] == history
        raise LoopObserved

    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', model)
    with pytest.raises(LoopObserved):
        await execute_chat(handler=h, request=request, runtime=runtime)
    assert len(main_rounds) == 3 and len(review_rounds) == 1
    assert p.ecom_workflow is None
    legacy.assert_not_awaited()
    h._execute_tool_calls.assert_not_awaited()
    p.adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_loop_repairs_proposal_without_repeating_unchanged_blind_extraction(monkeypatch):
    from core.config import get_settings
    settings=get_settings().model_copy(update={'skill_catalog_enabled':False,'skill_runtime_enabled':False})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    boot,state,anchor,_=bootstrap();goal='请生成五张详情图'
    boot=replace(boot,original_goal=goal,require_coverage_review=True)
    runtime=actor();runtime.task_bootstrap=boot;runtime.task_id=str(state.root_task_id)
    p=prepared();p.messages=[{'role':'user','content':goal}]
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=p))
    h=handler();reviews=[];mains=[]
    request=replace(_request(),content=[TextPart(text=goal)],task_id=str(state.root_task_id),
        user_id=str(state.actor_user_id),conversation_id=str(state.conversation_id),context_anchor=anchor)
    async def model(current,tools,*args,**kwargs):
        if kwargs.get('task_review'):
            reviews.append(True)
            return json.dumps({'action':'plan','deliveries':[{'artifact_kind':'image','minimum':5,'evidence':['五张详情图']}]}),'',[],set()
        mains.append(True)
        if len(mains)==3:
            assert runtime.task_execution_state.requirements[0].minimum==5
            assert runtime.task_execution_state.model_rounds_used==4  # Includes this third main attempt; only one extraction.
            assert len(runtime.task_requirement_review.attempts)==2
            raise LoopObserved
        proposal={'action':'plan','applicable_method_ids':[],'requirements':[{'id':'images','description':'五张详情图',
            'artifact_kind':'image','minimum':4 if len(mains)==1 else 5,'evidence':['五张详情图']}]}
        return '','',[call('start_task_execution',proposal,'proposal-'+str(len(mains)))],set()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    with pytest.raises(LoopObserved):await execute_chat(handler=h,request=request,runtime=runtime)
    assert len(mains)==3 and len(reviews)==1
    h._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_original_manual_prompt_method_reaches_blind_review_for_style_fragment(monkeypatch,original_source):
    from schemas.message import ImagePart
    from core.config import get_settings
    settings=get_settings().model_copy(update={'skill_catalog_enabled':True,'skill_runtime_enabled':True,
        'skill_recommendations_enabled':False})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    source=original_source;key='reference-image-prompts';goal='要风景类型的'
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource',lambda *_:source)
    boot,state,anchor,_=bootstrap();boot=replace(boot,original_goal=goal,require_coverage_review=True)
    runtime=actor();runtime.task_bootstrap=boot;runtime.task_id=str(state.root_task_id)
    p=prepared();p.messages=[{'role':'system','content':'原上下文'},
        {'role':'user','content':[{'type':'text','text':goal},{'type':'image_url','image_url':{'url':'https://example.invalid/original.png'}}]}]
    history=copy.deepcopy(p.messages)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=p))
    h=handler();h.org_id=None
    request=replace(_request(),content=[TextPart(text=goal),ImagePart(url='https://example.invalid/original.png')],
        selected_skill=SkillSelection(skill_id=key,revision=source.values[key].revision),
        task_id=str(state.root_task_id),conversation_id=str(state.conversation_id),user_id=str(state.actor_user_id),context_anchor=anchor)
    rounds=[]
    async def model(current,tools,*args,**kwargs):
        rounds.append(kwargs.get('task_review',False))
        if kwargs.get('task_review'):
            data=json.loads(current.messages[1]['content'])
            assert data['original_user_request']==goal and data['has_user_image_references']
            assert data['explicit_user_method_choices']==[{'skill_id':key,'name':source.values[key].catalog_metadata.name,
                'revision':source.values[key].revision}]
            assert 'proposal' not in data
            return json.dumps({'action':'plan','deliveries':[{'artifact_kind':'text','minimum':1,'evidence':[goal]}]}),'',[],set()
        if len(rounds)==1:
            return '','',[call('start_task_execution',{'action':'plan','applicable_method_ids':[key],
                'requirements':[{'id':'prompts','description':'六套完整提示词','artifact_kind':'text','minimum':1,
                    'item_count':6,'evidence':[goal]}]},'style-start')],set()
        assert runtime.task_execution_state.original_goal==goal
        assert [(r.artifact_kind,r.item_count) for r in runtime.task_execution_state.requirements if r.id.startswith('user.')]==[('text',6)]
        assert any(b.skill_key==key for b in runtime.task_execution_state.method_bindings)
        assert [m for m in p.messages if m in history]==history and not runtime.task_execution_state.operations
        raise LoopObserved
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    with pytest.raises(LoopObserved):await execute_chat(handler=h,request=request,runtime=runtime)
    assert rounds==[False,True,False]
    h._execute_tool_calls.assert_not_called()
