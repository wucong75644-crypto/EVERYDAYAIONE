"""Stopping advice must survive replay without narrowing legitimate retrieval."""
import pytest
from services.knowledge_workspace.search_progress import feedback
from tests.test_knowledge_derivation import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db)
from tests.test_knowledge_chat_v3 import start,ref

@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
 from core.config import get_settings
 s=get_settings();monkeypatch.setattr(s,'dashscope_api_key',None);monkeypatch.setattr(s,'knowledge_derivation_enabled',True)


def result(handles=(),cursor=None):return {'items':[{'handle':x} for x in handles],'next_cursor':cursor,'stop_reason':None}


def test_root_misses_stop_but_new_topic_and_other_root_remain_searchable():
 state={}
 assert not feedback(state,'a',None,True,{'query':'未知A'},result())['stop_reason']
 assert feedback(state,'a','root-object',True,{'query':'未知B'},result())['stop_reason']=='no_relevant_evidence'
 assert not feedback(state,'b',None,True,{'query':'未知B'},result())['stop_reason']
 hit=feedback(state,'a',None,True,{'query':'真实规则'},result(['new']))
 assert not hit['stop_reason'] and not state['last_search_empty_stop']


def test_narrow_and_route_misses_do_not_stop_wider_evidence_search():
 state={}
 for query in ['未知A','未知B']:
  assert feedback(state,'a','leaf',False,{'query':query},result())['stop_reason'] is None
  assert feedback(state,'a',None,True,{'query':query,'mode':'route'},result())['stop_reason'] is None
 assert feedback(state,'a',None,True,{'query':'改词'},result(['hit']))['items']


def test_duplicate_hits_advise_no_more_search_but_pagination_is_not_cut_off():
 state={};feedback(state,'r',None,True,{'query':'A'},result(['one']))
 assert feedback(state,'r',None,True,{'query':'B'},result(['one'],'next'))['stop_reason'] is None
 assert feedback(state,'r',None,True,{'query':'B','cursor':'next'},result(['two']))['stop_reason'] is None
 assert feedback(state,'r',None,True,{'query':'C'},result(['one','two']))['stop_reason']=='no_new_evidence'


def test_real_search_feedback_persists_replay_and_keeps_expand_direct_context_and_sources(derived_db):
 from services.knowledge_workspace.chat import KnowledgeRun
 make,_=derived_db;r=make();root=create(r,title='测试目录');branch=create(r,root['container_id'],root['id'],'普通分类');leaf=create(r,root['container_id'],branch['id'],'空节点')
 useful=r.command(command('create_object',{'container_id':root['container_id'],'parent_id':root['id'],'kind':'document','title':'拼多多漏发处理','body':'核对订单与照片后补寄；食品拆封不适用。','position':{'x':300,'y':0}}))['object_id']
 run,mid,cid=start(make,r,[ref(root)]);rh=run.introduction()['roots'][0]['handle'];lh=next(i['handle'] for i in run.snapshot['items'] if i['type']=='object' and i['id']==str(leaf['id']))
 for word in ['未知A','未知B']:
  miss=run.execute('search',{'root_handle':rh,'scope_handle':lh,'query':word});assert miss['stop_reason']=='not_found_in_scope'
 found=run.execute('search',{'root_handle':rh,'scope_handle':lh,'expand':True,'query':'漏发'});assert found['stop_reason']=='not_found_in_scope'
 broad=run.execute('search',{'root_handle':rh,'query':'漏发'});assert broad['items']
 h=next(i['handle'] for i in broad['items'] if ('object='+str(useful)+'&') in i['source']['url'])
 body=run.execute('read',{'root_handle':rh,'item_handles':[h],'context':True})
 assert '食品拆封' in body['items'][0]['body'];assert body['items'][0]['source']['url'] in run.evidence_footer()
 assert run.execute('search',{'root_handle':rh,'query':'独角兽不存在A'})['stop_reason']=='not_found_in_scope'
 replay=KnowledgeRun.start(r,conversation_id=cid,input_message_id=mid)
 assert replay.execute('search',{'root_handle':rh,'query':'独角兽不存在B'})['stop_reason']=='no_relevant_evidence'
 # A miss for one subject never disables a new valid subject in the same turn.
 assert replay.execute('search',{'root_handle':rh,'query':'拼多多漏发处理'})['items']


def test_missing_only_reply_has_short_footer_and_does_not_claim_document_read(derived_db):
 r=derived_db[0]();root=create(r);run,_,_=start(derived_db[0],r,[ref(root)]);rh=run.introduction()['roots'][0]['handle']
 for word in ['未提供甲','未提供乙']:last=run.execute('search',{'root_handle':rh,'query':word})
 assert last['stop_reason']=='no_relevant_evidence'
 assert run.evidence_footer()=='\n\n本次查询范围内未找到相关资料。'


def test_empty_pagination_is_not_a_root_miss_and_route_hit_clears_stale_footer_state():
 state={}
 page=feedback(state,'r',None,True,{'query':'A','cursor':'last'},result())
 assert page['stop_reason']=='no_new_evidence' and not state['last_search_empty_stop']
 assert feedback(state,'r',None,True,{'query':'B'},result())['stop_reason'] is None
 feedback(state,'r',None,True,{'query':'C'},result())
 assert state['last_search_empty_stop']
 feedback(state,'r',None,True,{'query':'有效目录','mode':'route'},result(['route']))
 assert not state['last_search_empty_stop']
