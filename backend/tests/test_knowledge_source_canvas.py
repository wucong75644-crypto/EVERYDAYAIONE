"""Source presentation and processing boundary, with no real model calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from services.knowledge_workspace import xmind
from services.knowledge_workspace.derivation_jobs import DerivationWorker
from tests.test_knowledge_xmind import archive


def test_disabled_ai_processing_never_scans_or_dispatches(monkeypatch):
 from services.knowledge_workspace import derivation_jobs
 monkeypatch.setattr(derivation_jobs,'get_settings',lambda:SimpleNamespace(knowledge_ai_processing_enabled=False))
 pool=Mock();worker=DerivationWorker(pool)
 assert worker.candidates()==[]
 assert asyncio.run(worker.execute({'phase':'file'})) is None
 assert asyncio.run(worker.execute({})) is None
 pool.connection.assert_not_called()


def source_archive():
 return archive([{'theme':{'centralTopic':{'properties':{'fo:color':'#FFFFFF','svg:fill':'#112233'}}},'rootTopic':{
 'id':'r','title':'完整原文','boundaries':[{'range':'(0,1)','title':'原始外框'}],'structureClass':'org.xmind.ui.logic.left','style':{'properties':{'fo:font-size':'24pt'}},
 'children':{'attached':[{'id':'a','title':'ERP编码 XYDSLBQB01-01','customWidth':300,
 'attributedTitle':[{'text':'ERP编码 '},{'text':'XYDSLBQB01-01','fo:color':'#F22816FF'}]}, {'id':'b','title':'第二个节点'}]}},
 'relationships':[{'id':'rel','end1Id':'a','end2Id':'b','title':'人工建立的关联'}]}],extra={})


def test_import_maps_source_style_rich_text_direction_and_relationships():
 plan=xmind.parse(source_archive())
 assert [t['title'] for t in plan.topics]==['完整原文','ERP编码 XYDSLBQB01-01','第二个节点']
 assert [t['parent_index'] for t in plan.topics]==[-1,0,0]
 assert plan.topics[0]['presentation']['structure']=='left'
 assert plan.topics[1]['x']<plan.topics[0]['x']
 assert plan.topics[0]['presentation']['style']['font_size']==32
 assert plan.topics[0]['presentation']['style']['background']=='#112233'
 assert plan.topics[1]['presentation']['width']==300
 assert plan.topics[1]['presentation']['title_format']['runs'][1]['color']=='#F22816'
 assert plan.relations==[{'source_index':1,'target_index':2,'appearance':{'text':'人工建立的关联'}}]


def test_detached_topic_stays_independent_and_boundary_keeps_exact_members():
 raw=archive([{'rootTopic':{'id':'r','title':'根','children':{'attached':[{'id':'a','title':'A'},{'id':'b','title':'B'}],
 'detached':[{'id':'f','title':'自由主题','children':{'attached':[{'id':'fc','title':'自由主题的子节点'}]}}]},
 'boundaries':[{'range':'(0,1)','title':'原始外框'}]}}],extra={})
 plan=xmind.parse(raw)
 assert [t['parent_index'] for t in plan.topics]==[-1,0,0,-1,3]
 assert plan.topics[0]['boundaries']==[{'title':'原始外框','members':[1,2]}]
 assert plan.topics[0]['sort_order']<plan.topics[3]['sort_order']


def test_legacy_ai_prepare_endpoint_is_closed_without_calling_model(monkeypatch):
 from fastapi import Response
 from api.routes import knowledge
 from services.knowledge_workspace import content_input
 from unittest.mock import AsyncMock
 from uuid import uuid4
 monkeypatch.setattr(knowledge,'enabled',lambda ctx:True)
 monkeypatch.setattr(knowledge,'get_settings',lambda:SimpleNamespace(knowledge_ai_processing_enabled=False))
 model=AsyncMock();monkeypatch.setattr(content_input,'prepare',model)
 response=asyncio.run(knowledge.content_plan(content_input.ContentInput(container_id=uuid4(),text='人工原文'),SimpleNamespace(),SimpleNamespace(),Response()))
 assert response.status_code==409
 model.assert_not_called()


def test_orienting_source_layout_preserves_tree_for_all_supported_directions():
 for source,direction in [('org.xmind.ui.logic.left','left'),('org.xmind.ui.org-chart.down','down'),('org.xmind.ui.org-chart.up','up'),('org.xmind.ui.map.unbalanced','balanced')]:
  plan=xmind.parse(archive([{'rootTopic':{'id':'r','title':'根','structureClass':source,'children':{'attached':[{'id':'a','title':'A'},{'id':'b','title':'B'}]}}}],extra={}))
  root,a,b=plan.topics
  assert [t['parent_index'] for t in plan.topics]==[-1,0,0]
  if direction=='left':assert a['x']<root['x'] and b['x']<root['x']
  elif direction=='down':assert a['y']>root['y'] and b['y']>root['y']
  elif direction=='up':assert a['y']<root['y'] and b['y']<root['y']
  else:assert a['x']>root['x'] and b['x']<root['x']
