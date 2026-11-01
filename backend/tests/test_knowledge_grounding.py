"""Saved-knowledge grounding contracts; chat answers no longer call this reviewer."""
import asyncio
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


def test_noncontiguous_quote_lines_are_separate_exact_fragments():
 facts=[{'key':'raw','kind':'original','text':'*申请类型\n说明\n*费用金额'}]
 value={'checks':[verdict(quote='*申请类型\n*费用金额')]}
 evidence=grounding.validate_audit(value,['两项带星号。'],facts)
 assert [q['quote'] for q in evidence[0]]==['*申请类型','*费用金额']
 value['checks'][0]['evidence'][0]['quote']='*申请类型\n... *费用金额'
 with pytest.raises(grounding.GroundingError,match='GROUNDING_QUOTE_NOT_IN_SOURCE'):grounding.validate_audit(value,['两项带星号。'],facts)
