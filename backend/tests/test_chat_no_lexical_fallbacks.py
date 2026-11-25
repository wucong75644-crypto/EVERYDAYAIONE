"""Words never stand in for a semantic decision or an authenticated receipt."""
from types import SimpleNamespace

import pytest

from schemas.message import TextPart
from services.handlers.chat import content_tools as tools
from services.task_execution import item_confirmation
from tests.semantic_fixtures import semantic_request


@pytest.mark.parametrize('text', [
    '第一个和第三个帮我做成图片', '不要生图，只写文字', '确认第2条，可以开始生成',
    '再来一次', '把刚生成的图改成蓝色', '参考图一，给图二加些波点',
    'use the first and third designs', 'make the background lighter',
])
def test_unclassified_words_do_not_forge_a_host_action(text):
    request = SimpleNamespace(content=[TextPart(text=text)])
    assert not tools.image_execution_request(request)
    assert not tools.image_writing_only_request(request)
    assert not tools.selects_previous_item(request)
    assert not tools.repeats_saved_image(request)
    assert not tools.requests_generated_image_edit(request)
    directive = item_confirmation.directive(request, None)
    assert not directive.approved and not directive.wait_for and not directive.wait_all


@pytest.mark.parametrize('text', ['请生成图片', '这段原文写着“只写文字，不生图”', '底色淡点儿'])
@pytest.mark.parametrize('intent', ['write', 'execute_saved', 'edit', 'repeat'])
def test_consumers_keep_the_frozen_decision_independent_of_word_order(text, intent):
    request = semantic_request(SimpleNamespace(content=[TextPart(text=text)]), intent)
    assert tools.image_writing_only_request(request) is (intent == 'write')
    assert tools.image_execution_request(request) is (intent != 'write')
    assert tools.selects_previous_item(request) is (intent in {'execute_saved', 'repeat'})
    assert tools.requests_generated_image_edit(request) is (intent == 'edit')


@pytest.mark.asyncio
async def test_missing_semantics_does_not_upgrade_a_legacy_checkpoint_protocol():
    from tests.test_model_directed_loop import case, execute
    from tests.test_task_execution_model_bridge import call
    from services.task_execution import content_objects as content
    runtime, prepared, handler, request = case()
    prepared.content_controls_available = True
    await execute(runtime, prepared, handler, request, [call(content.SAVE, {
        'document': {'title':'旧入口稿件', 'items':[{'title':'甲','body':'完整原文'}]},
    }, 'save')])
    assert '_media_decisions_v1' not in runtime.checkpoint_base
    assert content.catalog(runtime.task_execution_state)
    handler._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('text', ['青色优先', '字别太小', '保留封面', '我喜欢蓝色', '记住这个'])
def test_memory_eligibility_does_not_depend_on_business_or_preference_words(text):
    from services.memory.l1_extractor import _should_extract
    from services.memory.pipeline_scheduler import PipelineScheduler
    assert _should_extract(text)
    assert PipelineScheduler._should_extract([{'role':'user','content':text}])
    assert not PipelineScheduler._should_extract([{'role':'assistant','content':text}])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['alert','cross','trend','compare','ratio','distribution'])
async def test_erp_consumes_typed_query_shape_without_overriding_it_from_words(kind):
    from unittest.mock import AsyncMock, MagicMock, patch
    from services.agent.erp_agent import ERPAgent
    agent = ERPAgent(db=MagicMock(), user_id='u1', conversation_id='c1', org_id='org1')
    params = {'doc_type':'stock','query_type':kind,'platform':'jd'}
    with patch.object(agent, '_llm_extract', AsyncMock(return_value=([('warehouse',params)],None,'parallel'))):
        plan = await agent._extract_plan('这是原文里的一段“淘宝订单趋势”的引用，请按本轮实际要求处理。')
    assert plan is not None and not plan.degraded
    assert plan.steps[0].params['query_type'] == kind
    assert plan.steps[0].params['platform'] == 'jd'
    agent.db.table.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['库存最多的十个商品','来看看仓里积着哪些','查询SKU价格','供应商联系方式'])
async def test_department_missing_typed_action_never_guesses_or_reads(text):
    from unittest.mock import AsyncMock, MagicMock
    from services.agent.departments.warehouse_agent import WarehouseAgent
    agent = WarehouseAgent(db=MagicMock(), user_id='user', org_id='org')
    agent._dispatch = AsyncMock()
    result = await agent.execute(text, dag_mode=True)
    assert result.status == 'error' and result.error_message == 'ERP_DOCUMENT_TYPE_REQUIRED'
    agent._dispatch.assert_not_awaited()
    agent.db.table.assert_not_called()
