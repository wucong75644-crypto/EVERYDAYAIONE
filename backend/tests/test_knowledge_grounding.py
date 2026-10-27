"""Public grounding contracts use synthetic facts and isolated provider transports."""
import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from services.knowledge_workspace import grounding,derivation


def verdict(index=0,quote='未拆封且24小时内可退。',**kw):
 return {'index':index,'supported':True,'relevant':True,'evidence':[{'key':'raw','quote':quote}],**kw}


@pytest.mark.parametrize('checks,error',[
 ([], 'INVALID_GROUNDING_CHECKS'),
 ([verdict(index=True)],'INVALID_GROUNDING_INDEX'),
 ([verdict(quote='任何商品随时可退。')],'GROUNDING_QUOTE_NOT_IN_SOURCE'),
 ([verdict(supported='true')],'INVALID_GROUNDING_VERDICT'),
 ([verdict(supported=False)],'UNSUPPORTED_KNOWLEDGE_CONTENT'),
 ([verdict(relevant=False)],'UNSUPPORTED_KNOWLEDGE_CONTENT'),
 ([verdict(evidence=[])],'UNSUPPORTED_KNOWLEDGE_CONTENT'),
])
def test_bad_or_ungrounded_verdict_never_releases(checks,error,monkeypatch):
 async def response(*args,**kwargs):return {'checks':checks}
 monkeypatch.setattr(grounding,'request',response)
 with pytest.raises(grounding.GroundingError,match=error):
  asyncio.run(grounding.audit(['任何商品随时可退。'],[{'key':'raw','text':'未拆封且24小时内可退。'}],'退货条件？'))


def test_checked_coverage_and_exact_quote(monkeypatch):
 async def response(*args,**kwargs):return {'checks':[verdict(),verdict()]}
 monkeypatch.setattr(grounding,'request',response)
 with pytest.raises(grounding.GroundingError,match='INVALID_GROUNDING_INDEX'):
  asyncio.run(grounding.audit(['未拆封且24小时内可退。','条件如上'],[{'key':'raw','text':'未拆封且24小时内可退。'}],'退货条件？'))


def test_request_rechecks_authorization_after_response(monkeypatch):
 from core.config import get_settings
 monkeypatch.setattr(get_settings(),'dashscope_api_key','synthetic')
 checks=[]
 def authorize():
  checks.append(True)
  if len(checks)==2:raise ValueError('PERMISSION_OR_SOURCE_CHANGED')
 async def respond(request):return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
 async def scenario():
  async with httpx.AsyncClient(transport=httpx.MockTransport(respond),base_url='https://synthetic.invalid') as client:
   async def get():return client
   monkeypatch.setattr(derivation._client,'get',get)
   with pytest.raises(ValueError,match='PERMISSION_OR_SOURCE_CHANGED'):await grounding.request([],authorize=authorize)
 asyncio.run(scenario());assert len(checks)==2


def test_release_corrects_once_and_outage_does_not_return_draft(monkeypatch):
 facts=[{'key':'raw','text':'未拆封且24小时内可退。','source':{'url':'/knowledge?object=synthetic'}}]
 run=SimpleNamespace(grounding_sources=lambda:facts,revalidate=lambda:None,normalize_citations=lambda t:t)
 attempts=[]
 async def audit(candidate,*args,**kwargs):
  attempts.append(candidate)
  if candidate!=['未拆封且24小时内可退。']:raise grounding.GroundingError('UNSUPPORTED_KNOWLEDGE_CONTENT')
 async def correction(*args,**kwargs):return {'answer':'未拆封且24小时内可退。'}
 monkeypatch.setattr(grounding,'audit',audit);monkeypatch.setattr(grounding,'request',correction)
 assert asyncio.run(grounding.release_answer(run,'任何商品随时可退。','退货条件？'))=='未拆封且24小时内可退。'
 assert len(attempts)==2
 async def outage(*args,**kwargs):raise httpx.ConnectError('synthetic unavailable')
 monkeypatch.setattr(grounding,'audit',outage)
 result=asyncio.run(grounding.release_answer(run,'任何商品随时可退。','退货条件？'))
 assert '任何商品随时可退' not in result and '未能通过' in result and '未拆封且24小时内可退。' in result and '```text' in result


def test_permission_change_cannot_be_converted_into_fallback(monkeypatch):
 run=SimpleNamespace(grounding_sources=lambda:[{'key':'raw','text':'private'}],normalize_citations=lambda t:t,revalidate=lambda:(_ for _ in ()).throw(ValueError('PERMISSION_OR_SOURCE_CHANGED')))
 async def denied(*args,**kwargs):raise ValueError('PERMISSION_OR_SOURCE_CHANGED')
 monkeypatch.setattr(grounding,'audit',denied)
 with pytest.raises(ValueError,match='PERMISSION_OR_SOURCE_CHANGED'):asyncio.run(grounding.release_answer(run,'private','question'))


@pytest.mark.asyncio
async def test_verification_cancel_stops_pending_provider(monkeypatch):
 from services.handlers.chat.execution_engine import _verify_knowledge_answer
 started=asyncio.Event();closed=asyncio.Event();cancel=asyncio.Event()
 async def slow(*args,**kwargs):
  started.set()
  try:await asyncio.Event().wait()
  finally:closed.set()
 monkeypatch.setattr(grounding,'release_answer',slow)
 task=asyncio.create_task(_verify_knowledge_answer(object(),'draft','q',{},cancel,None))
 await started.wait();cancel.set()
 with pytest.raises(asyncio.CancelledError):await task
 assert closed.is_set()


@pytest.mark.asyncio
async def test_draft_and_reasoning_never_stream_or_enter_checkpoint_totals():
 from services.handlers.chat.execution_engine import _read_turn
 from services.handlers.chat.execution_sink import CollectingExecutionSink
 from services.handlers.chat.stream_session import StreamTotals
 async def stream(**kwargs):
  yield SimpleNamespace(content='未经支持的结论',thinking_content='猜测业务含义',tool_calls=None,prompt_tokens=1,completion_tokens=1,credits_consumed=None,finish_reason='stop')
 prepared=SimpleNamespace(adapter=SimpleNamespace(stream_chat=stream),messages=[],stream_kwargs={},knowledge_grounding_run=object())
 sink=CollectingExecutionSink();totals=StreamTotals();blocks=[]
 text,thinking,_,_=await _read_turn(prepared,[],asyncio.Event(),sink,totals,blocks,None)
 assert text=='未经支持的结论' and thinking=='猜测业务含义'
 assert sink.text==sink.thinking==totals.text==totals.thinking=='' and not blocks


def test_extractive_fallback_is_not_the_rejected_draft_and_keeps_exact_bytes():
 sources=[{'key':'raw','title':'测试资料','kind':'file','text':'仅限未拆封。\r\n\r\n\r\n超过24小时需核实。'}]
 value=derivation.extractive_fallback(sources)
 assert value['_grounding_fallback'] and not value['tags']
 assert '未通过核验' in value['sections'][0]['title']
 for section in value['sections']:
  assert section['grounding_mode']=='extractive_fallback'
  assert section['text'] in sources[0]['text']
  assert section['evidence'][0]['quote']==section['text']


def test_model_cannot_self_declare_verified_metadata():
 from services.knowledge_workspace.chunking import validate_output
 with pytest.raises(ValueError,match='INVALID_MODEL_SECTION'):
  validate_output({'sections':[{'title':'虚构','text':'自动批准','source_refs':['raw'],'grounding_version':grounding.VERSION,'evidence':[]}],'tags':[]},[{'key':'raw','text':'申请','kind':'manual'}])


def test_metadata_cannot_be_laundered_into_original_evidence():
 facts=[{'key':'navigation','text':'付款流程/档案查询','kind':'metadata'}]
 value={'checks':[{'index':0,'supported':True,'relevant':True,'evidence':[{'key':'navigation','quote':'档案查询','kind':'original'}]}]}
 result=grounding.validate_audit(value,['档案查询'],facts)
 assert result[0][0]['kind']=='metadata'


@pytest.mark.asyncio
async def test_budget_wrap_up_passes_same_gate(monkeypatch):
 from services.handlers.chat.execution_engine import _apply_budget_stop
 from services.handlers.chat.execution_sink import CollectingExecutionSink
 from services.handlers.chat.stream_session import StreamTotals
 monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up',lambda **kwargs:asyncio.sleep(0,result='未经支持的停止总结'))
 async def release(*args,**kwargs):return '只有这个结果已经核验'
 monkeypatch.setattr(grounding,'release_answer',release)
 prepared=SimpleNamespace(budget=SimpleNamespace(stop_reason='max_turns'),knowledge_grounding_run=object(),text_content='question',adapter=object(),messages=[])
 sink=CollectingExecutionSink();totals=StreamTotals();blocks=[]
 await _apply_budget_stop(prepared,totals,blocks,sink)
 assert totals.text=='只有这个结果已经核验'
 assert '未经支持' not in str(sink.blocks)


@pytest.mark.parametrize('expression,expected',[('5-1.2-1.5','2.3'),('0.1+0.2','0.3'),('100/4','25'),('3*(4+2)','18')])
def test_decimal_arithmetic_is_not_an_llm_guess(expression,expected):
 from services.knowledge_workspace.calculation import calculate
 assert calculate(expression)==expected


def test_calculations_are_explicit_and_never_rewrite_source_or_assign_business_meaning():
 from services.knowledge_workspace.calculation import calculation_sources,calculate
 sources=[{'key':'raw','text':'原文5-1.2-1.5=2.8。','kind':'original'}]
 assert calculation_sources(sources,'原文说什么？') is sources
 result=calculation_sources(sources,'请核对算术是否一致')
 assert result[0]==sources[0] and result[1]['kind']=='calculation'
 assert '= 2.3' in result[1]['text'] and '原文等号右侧写作 2.8' in result[1]['text']
 assert '成本' not in result[1]['text'] and '运费' not in result[1]['text']
 with pytest.raises(ValueError):calculate('__import__("os")')
 with pytest.raises(ValueError):calculate('2**10000')


@pytest.mark.parametrize('requires_sources,expected',[ (True,'不足以核验'),(False,'普通回复') ])
def test_automatic_scope_cannot_bypass_by_skipping_all_reads(requires_sources,expected,monkeypatch):
 run=SimpleNamespace(grounding_sources=lambda:None,revalidate=lambda:None)
 async def classify(*args,**kwargs):return {'requires_sources':requires_sources}
 monkeypatch.setattr(grounding,'request',classify)
 assert expected in asyncio.run(grounding.release_answer(run,'普通回复','question'))


def test_invalid_no_read_classification_does_not_allow_answer(monkeypatch):
 run=SimpleNamespace(grounding_sources=lambda:None,revalidate=lambda:None)
 async def classify(*args,**kwargs):return {'requires_sources':'false'}
 monkeypatch.setattr(grounding,'request',classify)
 assert '未经核验' in asyncio.run(grounding.release_answer(run,'公司上限9999元','question'))



def test_long_original_fallback_has_unique_sections_without_dropping_text():
 from services.knowledge_workspace.chunking import validate_output
 text='仅限未拆封，超过24小时需核实。'*500
 sources=[{'key':'raw','title':'测试资料','text':text,'kind':'manual'}]
 value=derivation.extractive_fallback(sources)
 value.pop('_grounding_fallback')
 sections,_=validate_output(value,sources,verified=True)
 assert len(sections)>1 and len({s['id'] for s in sections})==len(sections)
 assert ''.join(s['text'] for s in sections)==text
 assert all(s['evidence'][0]['quote']==s['text'] for s in sections)


def test_only_completed_server_tool_steps_inform_no_source_routing():
 from services.handlers.chat.execution_engine import _completed_other_tools
 assert _completed_other_tools([
  {'type':'tool_step','tool_name':'erp_agent','status':'completed'},
  {'type':'tool_step','tool_name':'clock','status':'error'},
  {'type':'tool_step','tool_name':'knowledge_read','status':'completed'},
  {'type':'text','tool_name':'fake_agent','status':'completed'}])==['erp_agent']


def test_other_business_tool_chain_is_not_claimed_as_knowledge_verification(monkeypatch):
 run=SimpleNamespace(grounding_sources=lambda:None,revalidate=lambda:None)
 async def classify(messages,**kwargs):
  assert json.loads(messages[1]['content'])['executed_other_tools']==['erp_agent']
  return {'requires_sources':False}
 monkeypatch.setattr(grounding,'request',classify)
 usage={};answer=asyncio.run(grounding.release_answer(run,'工具已返回查询结果','查实时库存',usage=usage,other_tools=['erp_agent']))
 assert answer=='工具已返回查询结果' and usage['grounding_outcome']=='other_tool_chain'



def test_citation_list_punctuation_is_not_treated_as_a_factual_claim():
 text='费用类型和金额为必填。\n来源：[来源](/knowledge?object=a)、[来源](/knowledge?object=b)'
 assert grounding.answer_units(text)==['费用类型和金额为必填。']
 assert grounding.answer_units('来源：500元免审批。')==['来源：500元免审批。']



def test_raw_fallback_markdown_is_literal_and_cannot_close_its_fence():
 value=grounding.fallback([{'key':'raw','text':'```\n![不可信图片](https://example.invalid/pixel)\n<script>bad()</script>','source':{'url':'/knowledge?object=synthetic'}}])
 assert '````text\n```\n![不可信图片]' in value
 assert value.endswith('[来源](/knowledge?object=synthetic)')


def test_authorized_bare_citations_are_formatted_without_hiding_neighboring_claims():
 from types import SimpleNamespace
 import asyncio
 from services.knowledge_workspace.grounding import canonical_answer,answer_units
 run=SimpleNamespace(normalize_citations=lambda t:t)
 text=asyncio.run(canonical_answer(run,'尺寸75mm。\n来源：/knowledge?object=a&revision=1\n审批无需批准：[任意标签](/knowledge?object=a&revision=1)'))
 assert text.count('[来源](/knowledge?object=a&revision=1)')==2
 units=answer_units(text)
 assert any('审批无需批准' in unit for unit in units)
 assert not any('/knowledge?' in unit for unit in units)


def test_prune_removes_whole_failed_units_and_preserves_links():
 text='已核查范围：\n无法确认步骤。[来源](/knowledge?object=synthetic)\n请上传课件。'
 error=grounding.GroundingError('unsupported');error.rejected_indices={0,2}
 pruned=grounding.prune_rejected(text,error)
 assert pruned=='无法确认步骤。[来源](/knowledge?object=synthetic)'
 # Retained lines keep their citations and formatting.
 text='未读取外链[来源](/knowledge?object=synthetic)，无法确认步骤。\n无权限且不能读取。'
 error.rejected_indices={1}
 assert grounding.prune_rejected(text,error)==text.split('\n')[0]
 assert '无权限' not in grounding.prune_rejected(text,error)
 assert grounding.prune_rejected(text,ValueError()) is None
 error.rejected_indices={1}
 assert grounding.prune_rejected('**未读取。无权限。**\n无法确认。',error)=='无法确认。'


def test_pruned_answer_is_reaudited_and_never_released_on_failure(monkeypatch):
 facts=[{'key':'raw','text':'外部链接未读取。'}]
 run=SimpleNamespace(grounding_sources=lambda:facts,revalidate=lambda:None,normalize_citations=lambda t:t)
 seen=[]
 async def audit(units,*args,**kwargs):
  seen.append(units)
  if units==['无法确认步骤。']:return {}
  error=grounding.GroundingError('UNSUPPORTED_KNOWLEDGE_CONTENT');error.rejected_indices={1};raise error
 async def correction(*args,**kwargs):return {'answer':'无法确认步骤。\n无权限。'}
 monkeypatch.setattr(grounding,'audit',audit);monkeypatch.setattr(grounding,'request',correction)
 usage={}
 assert asyncio.run(grounding.release_answer(run,'无法确认步骤。\n无权限。','步骤？',usage=usage))=='无法确认步骤。'
 assert len(seen)==3 and usage['grounding_outcome']=='pruned'
 seen.clear()
 async def outage(units,*args,**kwargs):
  if units==['无法确认步骤。']:raise grounding.GroundingError('INVALID_GROUNDING_CHECKS')
  return await audit(units,*args,**kwargs)
 monkeypatch.setattr(grounding,'audit',outage)
 result=asyncio.run(grounding.release_answer(run,'无法确认步骤。\n无权限。','步骤？'))
 assert len(seen)==2 and '未能通过' in result and '无权限' not in result


def test_pruning_is_monotonic_and_stops_after_two_rechecks(monkeypatch):
 facts=[{'key':'raw','text':'外部入口未读取。'}]
 run=SimpleNamespace(grounding_sources=lambda:facts,revalidate=lambda:None,normalize_citations=lambda t:t)
 sizes=[]
 async def audit(units,*args,**kwargs):
  sizes.append(len(units))
  error=grounding.GroundingError('UNSUPPORTED_KNOWLEDGE_CONTENT');error.rejected_indices={len(units)-1};raise error
 async def correction(*args,**kwargs):return {'answer':'外链未读取。\n建议一。\n建议二。\n建议三。'}
 monkeypatch.setattr(grounding,'audit',audit);monkeypatch.setattr(grounding,'request',correction)
 answer=asyncio.run(grounding.release_answer(run,'外链未读取。\n建议一。\n建议二。\n建议三。','视频步骤？'))
 assert sizes==[4,4,3,2] and '未能通过' in answer and '建议一' not in answer


def test_added_parenthetical_interpretation_is_checked_as_an_independent_claim():
 text='物品到货后放进去（即分成两次发送）。'
 units=grounding.answer_units(text)
 assert '（即分成两次发送）' in units and len(units)==2
 error=grounding.GroundingError('unsupported');error.rejected_indices={1}
 assert grounding.prune_rejected(text+'\n不能确认批次。',error)=='不能确认批次。'
 # Business conditions and source identifiers keep their scope.
 assert grounding.answer_units('需审批（超过100元）。')==['需审批（超过100元）。']


def test_failed_role_explanation_can_only_be_replaced_by_checked_original_quote():
 facts=[{'key':'raw','kind':'original','text':'填写信息后由负责方报价，查看安排见表。','source':{'url':'/knowledge?object=synthetic'}}]
 value={'checks':[verdict(supported=False,quote='填写信息后由负责方报价，查看安排见表。')]}
 with pytest.raises(grounding.GroundingError) as caught:
  grounding.validate_audit(value,['指定人员每天查看并保证回复。'],facts)
 repaired,quoted=grounding.repair_rejected('指定人员每天查看并保证回复。',caught.value,facts)
 assert quoted and '指定人员' not in repaired and '每天' not in repaired and '保证回复' not in repaired
 assert facts[0]['text'] in repaired and '[来源](/knowledge?object=synthetic)' in repaired
 # Unrelated material is never inserted just because it is literal.
 value['checks'][0]['relevant']=False
 with pytest.raises(grounding.GroundingError) as caught:
  grounding.validate_audit(value,['指定人员每天查看并保证回复。'],facts)
 assert grounding.repair_rejected('指定人员每天查看并保证回复。',caught.value,facts)==(None,set())


def test_noncontiguous_quote_lines_are_separate_exact_fragments():
 facts=[{'key':'raw','kind':'original','text':'*申请类型\n说明\n*费用金额'}]
 value={'checks':[verdict(quote='*申请类型\n*费用金额')]}
 evidence=grounding.validate_audit(value,['两项带星号。'],facts)
 assert [q['quote'] for q in evidence[0]]==['*申请类型','*费用金额']
 value['checks'][0]['evidence'][0]['quote']='*申请类型\n... *费用金额'
 with pytest.raises(grounding.GroundingError,match='GROUNDING_QUOTE_NOT_IN_SOURCE'):grounding.validate_audit(value,['两项带星号。'],facts)


def test_model_self_labelled_quote_does_not_get_server_literal_provenance():
 units,_=grounding.nonliteral_units('原文片段：我可以保证每天回复。',set())
 assert units==['原文片段：我可以保证每天回复。']
 facts=[{'key':'raw','kind':'original','text':'由负责方报价，查看安排见表。'}]
 value={'checks':[verdict(supported=False,quote=facts[0]['text'])]}
 with pytest.raises(grounding.GroundingError) as caught:grounding.validate_audit(value,['我可以保证每天回复。'],facts)
 text,literal=grounding.repair_rejected('我可以保证每天回复。',caught.value,facts)
 assert grounding.nonliteral_units(text,literal)[0]==[]
 assert '保证' not in text and facts[0]['text'] in text
