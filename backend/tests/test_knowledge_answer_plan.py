"""Fast synthesis uses real snapshot/RLS reads, never model-granted scope."""
import asyncio
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest
from tests.test_knowledge_search_progress import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,start,ref)
from services.knowledge_workspace.answer_plan import collect,eligible,prepare,invalidate_for_steer,PREFIX

@pytest.fixture(autouse=True)
def settings(monkeypatch):
 from core.config import get_settings
 monkeypatch.setattr('services.knowledge_workspace.evidence_assessment.assess', lambda q, items: {'status':'resolved','needs':[{'question':q,'status':'resolved','handles':[i['handle'] for i in items]}]})
 s=get_settings();monkeypatch.setattr(s,'knowledge_derivation_enabled',True);monkeypatch.setattr(s,'knowledge_semantic_enabled',False);monkeypatch.setattr(s,'dashscope_api_key',None)


def fixture(r):
 root=create(r,title='售后目录');leaf=create(r,root['container_id'],root['id'],'拼多多漏发')
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':1,'body':'拼多多漏发：先核对订单数量和照片，免费补寄；食品拆封不适用，需人工确认。'}))
 return root,leaf


def test_routing_keeps_complex_actions_erp_and_followups_out_of_fast_generation():
 assert eligible('拼多多漏发怎么处理，食品拆封适用吗？')
 for q in ['你好','比较全部售后规则','请详细查漏发规则','修改售后规则要求','查询昨天拼多多销售额','实时库存多少','创建售后目录']:
  assert not eligible(q),q


def test_program_reads_evidence_once_and_preserves_conditions_and_fixed_links(derived_db):
 r=derived_db[0]();root,_=fixture(r);run,_,_=start(derived_db[0],r,[ref(root)])
 result=collect(run,'拼多多漏发如何处理？食品拆封可以吗？')
 assert result['mode']=='answer' and len(result['items'])==1
 body=result['items'][0]['body'];assert all(x in body for x in ['订单','照片','免费','食品','人工'])
 assert result['items'][0]['source']['url'] in run.evidence_footer()
 with r.cursor() as c:
  c.execute('SELECT state FROM kb_retrieval_states WHERE snapshot_id=%s',(run.id,));assert c.fetchone()['state']['calls']==1


def test_no_match_is_bounded_and_does_not_import_other_root(derived_db):
 r=derived_db[0]();empty=create(r,title='空目录');other,_=fixture(r)
 run,_,_=start(derived_db[0],r,[ref(empty)])
 assert collect(run,'拼多多漏发如何处理？')['mode']=='expand'
 from core.exceptions import AppException
 with pytest.raises(AppException):start(derived_db[0],derived_db[0](1),[ref(other)])


def test_long_text_and_wide_matches_fall_back_instead_of_truncating_answer(derived_db):
 r=derived_db[0]();root,leaf=fixture(r)
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':2,'body':'\n'.join(f'拼多多漏发条件{i}：核对照片与订单。' for i in range(600))}))
 run,_,_=start(derived_db[0],r,[ref(root)]);partial=collect(run,'拼多多漏发如何处理？');assert partial['mode']=='expand'
 from services.knowledge_workspace.answer_plan import message
 packed=message(partial)['content'];assert partial['items'][0]['next_cursor'] in packed and partial['items'][0]['root_handle'] in packed
 for i in range(7):
  node=create(r,root['container_id'],root['id'],f'漏发政策{i}')
  r.command(command('save_document',{'object_id':node['id'],'expected_revision':1,'body':'拼多多漏发核对订单照片。'}))
 run,_,_=start(derived_db[0],r,[ref(root)]);assert collect(run,'拼多多漏发如何处理？')['reason']=='broad_evidence'


@pytest.mark.asyncio
async def test_engine_fast_answer_calls_model_once_with_no_tools(derived_db,monkeypatch):
 from services.handlers.chat.execution_engine import execute_chat
 from services.handlers.chat.execution_sink import CollectingExecutionSink
 from tests.test_chat_execution_engine import _request
 from services.tools.runtime_context import catalog_context
 from schemas.message import TextPart
 r=derived_db[0]();root,_=fixture(r);run,_,cid=start(derived_db[0],r,[ref(root)]);seen=[]
 async def stream(**kwargs):
  seen.append(kwargs)
  yield SimpleNamespace(content='核对订单照片后免费补寄；食品拆封转人工。',thinking_content=None,tool_calls=None,prompt_tokens=3,completion_tokens=2,credits_consumed=None,finish_reason='stop')
 context=catalog_context(r.scope.org_id)
 prepared=SimpleNamespace(execution_context=context,adapter=SimpleNamespace(stream_chat=stream,close=AsyncMock()),permission=SimpleNamespace(mode=SimpleNamespace(value='auto'),need_exit_attachment=False,get_reminder=lambda _:''),core_tools=[],stream_kwargs={},tool_context=SimpleNamespace(discovered_tools=set(),build_context_prompt=lambda:''),messages=[],budget=SimpleNamespace(stop_reason=None,turns_used=0))
 prepared.budget.use_turn=lambda:setattr(prepared.budget,'turns_used',prepared.budget.turns_used+1)
 monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=prepared))
 monkeypatch.setattr('services.knowledge_workspace.materialize.cleanup',lambda _:None)
 handler=SimpleNamespace(org_id=r.scope.org_id,_knowledge_run=run,_personal_context_allowed=True,_calculate_credits=lambda u:u['completion_tokens'])
 req=replace(_request(),content=[TextPart(text='拼多多漏发如何处理？食品拆封可以吗？')],user_id=r.scope.actor_user_id,conversation_id=cid)
 result=await execute_chat(handler=handler,request=req,sink=CollectingExecutionSink())
 assert len(seen)==1 and seen[0]['tools']==[]
 assert any(m.get('content','').startswith(PREFIX) for m in seen[0]['messages'] if isinstance(m.get('content'),str))
 assert '/knowledge?' in ''.join(p.text for p in result.parts if isinstance(p,TextPart))
 invalidate_for_steer(prepared,run);assert prepared.knowledge_answer_plan['mode']=='expand'


@pytest.mark.asyncio
async def test_skill_host_restrictions_and_other_actor_do_not_enter_fast_path(derived_db):
 from tests.test_chat_execution_engine import _request
 from schemas.message import TextPart
 r=derived_db[0]();root,_=fixture(r);run,_,_=start(derived_db[0],r,[ref(root)])
 req=replace(_request(),content=[TextPart(text='拼多多漏发怎么处理？')],user_id=r.scope.actor_user_id)
 handler=SimpleNamespace(_knowledge_run=run,org_id=r.scope.org_id,_personal_context_allowed=True)
 prepared=SimpleNamespace(execution_context=SimpleNamespace(context_scope='user',authorized_tool_names={'unrelated_tool'}),messages=[])
 await prepare(handler,prepared,req);assert not hasattr(prepared,'knowledge_answer_plan')
 prepared.execution_context.authorized_tool_names=None
 await prepare(handler,prepared,req,SimpleNamespace(active={'manual-skill':object()}));assert not hasattr(prepared,'knowledge_answer_plan')
 from core.exceptions import AppException
 with pytest.raises(AppException):await prepare(handler,prepared,replace(req,user_id='another-user'))


def test_frozen_production_corpus_prefetches_originals_without_rollup_speculation(derived_db):
 from tests.knowledge_corpus import clone
 r=derived_db[0]();roots=clone(r);run,_,_=start(derived_db[0],r,[ref(roots['normal'])])
 plan=collect(run,'请查询知识库：拼多多订单漏发要先核对什么？食品拆封能按此流程处理吗？请给来源。')
 assert plan['mode']=='answer',plan
 assert plan['source_count']==1  # Explicit platform scope stays authoritative.
 assert sum(i['type']=='file' for i in run.snapshot['items'])==4
 text='\n'.join(i['body'] for i in plan['items'])
 assert '食品拆封' in text and '照片' in text
 media=collect(run,'扫描资料验证码是什么？食品拆封是否适用补寄？')
 assert media['mode']=='expand' and 'SCAN-1025' in '\n'.join(i['body'] for i in media['items'])
 assert '照片是否与' not in text
 missing,_,_=start(derived_db[0],r,[ref(roots['missing'])]);assert collect(missing,'橙鹿换货期限是多少？蓝鲸预约提前多久？')['mode']=='expand'


@pytest.mark.asyncio
async def test_replay_and_user_steering_do_not_restore_fast_mode_after_expansion(derived_db):
 from tests.test_chat_execution_engine import _request
 from schemas.message import TextPart
 from services.knowledge_workspace.answer_plan import message
 r=derived_db[0]();root,_=fixture(r);run,_,_=start(derived_db[0],r,[ref(root)])
 handler=SimpleNamespace(_knowledge_run=run,org_id=r.scope.org_id,_personal_context_allowed=True)
 req=replace(_request(),content=[TextPart(text='拼多多漏发怎么处理？')],user_id=r.scope.actor_user_id,replay_context={'messages':[]})
 prepared=SimpleNamespace(execution_context=SimpleNamespace(context_scope='user',authorized_tool_names=None),messages=[message({'mode':'missing','items':[]})])
 await prepare(handler,prepared,req);assert prepared.knowledge_answer_plan['mode']=='missing'
 invalidate_for_steer(prepared,run)
 await prepare(handler,prepared,req);assert prepared.knowledge_answer_plan['mode']=='expand'


def test_query_embedding_failure_does_not_become_definitive_no_match(derived_db,monkeypatch):
 from core.config import get_settings
 r=derived_db[0]();root=create(r,title='空目录');run,_,_=start(derived_db[0],r,[ref(root)])
 monkeypatch.setattr(get_settings(),'knowledge_semantic_enabled',True);monkeypatch.setattr(get_settings(),'dashscope_api_key','test-no-network')
 monkeypatch.setattr('services.knowledge_workspace.semantic.query_embedding',lambda _:None)
 assert collect(run,'橙鹿换货期限是多少？')['reason']=='semantic_unavailable'


def test_same_input_retry_reuses_evidence_only_after_live_authorization(derived_db):
 from services.knowledge_workspace.chat import KnowledgeRun
 from core.exceptions import AppException
 make,actors=derived_db;owner=make();root,_=fixture(owner)
 owner.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}))
 reader=make(1);run,mid,cid=start(make,reader,[ref(root)]);question='拼多多漏发如何处理？'
 first=collect(run,question);assert first['mode']=='answer'
 replay=KnowledgeRun.start(reader,conversation_id=cid,input_message_id=mid)
 for _ in range(3):assert collect(replay,question)['cache_hit']
 owner.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':'viewer','status':'removed'}))
 with pytest.raises(AppException):collect(replay,question)


def test_reference_targets_require_existing_follow_navigation_not_fast_absence(derived_db):
 r=derived_db[0]();root,leaf=fixture(r)
 target=r.command(command('create_object',{'container_id':root['container_id'],'kind':'topic','title':'外部总分支','body':'食品拆封另有条件','position':{'x':1000,'y':100}}))['object_id']
 r.command(command('connect',{'source_object_id':leaf['id'],'target_object_id':target,'relation_type':'reference'}))
 run,_,_=start(derived_db[0],r,[{'root':{'kind':'object','id':str(root['id'])},'policy':'latest_saved'}])
 assert collect(run,'拼多多漏发怎么处理？')['reason']=='reference_navigation'


def test_queries_use_saved_media_chunks_without_original_io_or_derivation(derived_db,monkeypatch):
 from tests.knowledge_corpus import clone
 from services.knowledge_workspace import media,files,derivation,materialize
 def forbidden(*args,**kwargs):raise AssertionError('question must not reopen or reparse originals')
 r=derived_db[0]();roots=clone(r)
 for module,name in [(media,'extract'),(media,'recognize'),(files,'read_version'),(derivation,'process'),(materialize,'materialize')]:
  monkeypatch.setattr(module,name,forbidden)
 run,_,_=start(derived_db[0],r,[ref(roots['normal'])])
 question='视频里要求核对什么？扫描资料验证码是什么？食品拆封可以吗？'
 plan=collect(run,question);assert plan['mode']=='expand'
 assert any(i['status'] != 'complete' for i in plan['items'])
 assert 'SCAN-1025' in '\n'.join(i['body'] for i in plan['items'])
 assert collect(run,question)['cache_hit']
 result=run.execute('search',{'root_handle':run.introduction()['roots'][0]['handle'],'query':'核对'})
 saved=next(i for i in result['items'] if i['type']=='generated')
 assert run.execute('read',{'root_handle':run.introduction()['roots'][0]['handle'],'item_handles':[saved['handle']],'context':True})['items'][0]['body']


def named_document(r,root,title='查询失败：全量库存分组历史记录',body='历史失败；需补充商品关键词，不代表当前规则。'):
 return r.command(command('create_object',{'container_id':root['container_id'],'parent_id':root['id'],'kind':'document','title':title,'body':body,'position':{'x':300,'y':0}}))['object_id']


def test_named_source_reads_matching_other_root_before_model_routing(derived_db):
 from services.knowledge_workspace.answer_plan import named_source_plan
 r=derived_db[0]();personal=create(r,title='个人知识库');team=create(r,title='团队知识库');oid=named_document(r,team)
 run,_,_=start(derived_db[0],r,[ref(personal),ref(team)])
 plan=named_source_plan(run,'请看“查询失败：全量库存分组历史记录”，这是成功方法吗？')
 assert plan['mode']=='expand' and plan['reason']=='explicit_named_sources'
 assert len(plan['items'])==1 and '历史失败' in plan['items'][0]['body']
 assert 'object='+str(oid) in plan['items'][0]['source']['url']
 assert plan['items'][0]['source']['url'] in run.evidence_footer()
 with r.cursor() as c:
  c.execute("SELECT count(*) n FROM kb_read_receipts WHERE snapshot_id=%s AND state='body_complete'",(run.id,));assert c.fetchone()['n']==1
 outside,_,_=start(derived_db[0],r,[ref(personal)])
 assert named_source_plan(outside,'请看“查询失败：全量库存分组历史记录”') is None


def test_named_source_ambiguity_and_directory_titles_do_not_guess(derived_db):
 from services.knowledge_workspace.answer_plan import named_source_plan
 r=derived_db[0]();a=create(r,title='团队知识库');b=create(r,title='另一个项目')
 named_document(r,a);named_document(r,b)
 run,_,_=start(derived_db[0],r,[ref(a),ref(b)])
 assert named_source_plan(run,'请看“查询失败：全量库存分组历史记录”') is None
 assert named_source_plan(run,'请看“团队知识库”') is None
 with r.cursor() as c:
  c.execute('SELECT count(*) n FROM kb_read_receipts WHERE snapshot_id=%s',(run.id,));assert c.fetchone()['n']==0


def test_named_source_keeps_pagination_rules_and_revocation(derived_db):
 from services.knowledge_workspace.answer_plan import named_source_plan
 from core.exceptions import AppException
 make,actors=derived_db;r=make();root=create(r,title='历史资料');oid=named_document(r,root,body='历史资料正文'*2000)
 r.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}))
 run,_,_=start(make,make(1),[ref(root)])
 plan=named_source_plan(run,'按“查询失败：全量库存分组历史记录”核对。')
 assert plan['items'][0]['status']=='partial' and plan['items'][0]['next_cursor']
 r.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':'viewer','status':'removed'}))
 with pytest.raises(AppException):named_source_plan(run,'查阅“查询失败：全量库存分组历史记录”')


@pytest.mark.asyncio
async def test_named_source_preparation_keeps_action_tool_loop_and_auth_policy(derived_db):
 from tests.test_chat_execution_engine import _request
 from schemas.message import TextPart
 r=derived_db[0]();root=create(r,title='历史资料');named_document(r,root);run,_,_=start(derived_db[0],r,[ref(root)])
 req=replace(_request(),content=[TextPart(text='参考“查询失败：全量库存分组历史记录”，查询今天库存。')],user_id=r.scope.actor_user_id)
 assert not eligible(req.content[0].text)
 handler=SimpleNamespace(_knowledge_run=run,org_id=r.scope.org_id,_personal_context_allowed=True)
 prepared=SimpleNamespace(execution_context=SimpleNamespace(context_scope='user',authorized_tool_names=None),messages=[])
 await prepare(handler,prepared,req)
 assert prepared.knowledge_answer_plan['mode']=='expand'
 assert any(m['content'].startswith(PREFIX) for m in prepared.messages)
 restricted=SimpleNamespace(execution_context=SimpleNamespace(context_scope='user',authorized_tool_names={'unrelated_tool'}),messages=[])
 await prepare(handler,restricted,req);assert not hasattr(restricted,'knowledge_answer_plan')


def test_unrelated_journal_does_not_block_precise_identifier_evidence(derived_db):
 r=derived_db[0]();root=create(r,title='验收资料')
 target=named_document(r,root,'RZ-M682 合成验收口令','RZ-M682 的验收口令为晨星682。')
 other=named_document(r,root,'ZQ-X983 工作记录','ZQ-X983 合成验收曾失败；只适用另一编号。')
 run,_,_=start(derived_db[0],r,[ref(root)])
 for item in run.snapshot['items']:
  if item['id']==str(other):item['journal_category']='work'
 plan=collect(run,'RZ-M682 的验收口令是什么？')
 assert plan['mode']=='answer' and len(plan['items'])==1
 assert 'object='+str(target) in plan['items'][0]['source']['url']
