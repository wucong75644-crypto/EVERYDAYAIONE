"""Synthetic archives only. No provider calls, personal files, or business URLs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
from pathlib import Path
import stat
from uuid import uuid4
import zipfile

import psycopg
import pytest
from core.exceptions import AppException
from services.knowledge_workspace import xmind
from services.knowledge_workspace.files import read_version
from tests.test_knowledge_project_delete import (project_delete_db,derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,document,fake,process)


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    from core.config import get_settings
    settings=get_settings()
    monkeypatch.setattr(settings,'dashscope_api_key',None)
    monkeypatch.setattr(settings,'knowledge_semantic_enabled',False)
    monkeypatch.setattr(settings,'knowledge_media_enabled',False)


def archive(sheets=None,extra=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        # Retrying the same synthetic file must not change its archive timestamp/hash.
        def write(name,data):
            info=zipfile.ZipInfo(name,(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,data)
        write('content.json',json.dumps(sheets or [{'rootTopic':{'id':'root','title':'合成培训','children':{'attached':[
          {'id':'a','title':'完整多行\n先核对单号，再确认收货。','branch':'folded','children':{'attached':[{'id':'long','title':'长主题'*100}]}},
          {'id':'b','image':{'src':'xap:resources/pixel.png'},'href':'https://example.test/document'},
        ],'summary':[{'id':'s','title':'合成概要'}]},'summaries':[{'range':'(0,1)','topicId':'s'}]}}],ensure_ascii=False))
        if extra is None:
            # Valid tiny PNG, generated from a synthetic pixel.
            from PIL import Image
            b=io.BytesIO();Image.new('RGB',(2,3),'red').save(b,format='PNG');extra={'resources/pixel.png':b.getvalue()}
        for key,value in extra.items():write(key,value)
    return output.getvalue()


def test_parse_preserves_multiline_untitled_media_order_summary_and_long_text():
    plan=xmind.parse(archive());assert plan.report()['topics']==5
    assert plan.report()['images']==1 and plan.report()['links']==1 and plan.report()['summaries']==1
    assert plan.topics[1]['body']=='完整多行\n先核对单号，再确认收货。'
    assert plan.topics[2]['body']=='长主题'*100 and len(plan.topics[2]['title'])==200
    assert plan.topics[3]['title']=='图片' and '未读取' in plan.topics[3]['body']
    assert plan.topics[4]['summary']==[1,3];assert plan.topics[1]['folded']


@pytest.mark.parametrize('path',['../escape','/absolute','resources/../escape','resources\\escape','resources/%2e%2e/escape','C:/escape'])
def test_unsafe_members_and_references_fail_closed(path):
    if '%' in path:
        data=archive([{'rootTopic':{'id':'r','image':{'src':'xap:'+path}}}],{'escape':b'x'})
    else:data=archive(extra={path:b'x'})
    with pytest.raises(AppException):xmind.parse(data)


@pytest.mark.parametrize('change',['missing','duplicate_id','bad_summary','unknown_children','text_limit','unsafe_link','depth'])
def test_invalid_content_and_missing_resources_are_not_partial_success(change):
    root={'id':'r','title':'r','children':{'attached':[{'id':'c','title':'c'}]}}
    if change=='missing':root['image']={'src':'xap:resources/missing.png'}
    if change=='duplicate_id':root['children']['attached'][0]['id']='r'
    if change=='bad_summary':root['summaries']=[{'range':'(0,9)','topicId':'none'}]
    if change=='unknown_children':root['children']['mystery']=[]
    if change=='text_limit':root['title']='a'*200001
    if change=='unsafe_link':root['href']='a'*2049
    if change=='depth':
        t=root
        for n in range(65):t['children']={'attached':[{'id':str(n)}]};t=t['children']['attached'][0]
    with pytest.raises(AppException):xmind.parse(archive([{'rootTopic':root}],{}))


def test_non_web_links_are_retained_without_clickable_canvas_link():
    plan=xmind.parse(archive([{'rootTopic':{'id':'r','href':'javascript:alert(1)'}}],{}))
    assert 'javascript:alert(1)' in plan.topics[0]['body'];assert 'link' not in plan.topics[0]
    assert any('非 HTTP' in w for w in plan.warnings)


def test_xml_supported_but_dtd_entities_and_duplicate_zip_are_rejected():
    def xml(data):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('content.xml',data)
        return b.getvalue()
    good=b'<xmap-content xmlns="urn:xmind"><sheet><title>Map</title><topic id="r"><title>Root</title><children><topics type="attached"><topic id="c"><title>Child</title><notes><plain>Instructions</plain></notes></topic></topics></children></topic></sheet></xmap-content>'
    assert xmind.parse(xml(good)).topics[1]['body']=='Child\n\nInstructions'
    for encoding in ('utf-8','utf-16'):
        bad='<!DOCTYPE x [<!ENTITY e "secret">]><xmap-content/>'
        with pytest.raises(AppException):xmind.parse(xml(bad.encode(encoding)))
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('content.json','[]');z.writestr('content.json','[]')
    with pytest.raises(AppException):xmind.parse(b.getvalue())


def test_special_and_encrypted_archive_entries_rejected():
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        entry=zipfile.ZipInfo('symlink');entry.external_attr=(stat.S_IFLNK|0o777)<<16
        z.writestr(entry,'/etc/passwd');z.writestr('content.json','[]')
    with pytest.raises(AppException):xmind.parse(b.getvalue())


@pytest.fixture(scope='module')
def import_db(project_delete_db):
    make,actors=project_delete_db
    with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/333_knowledge_xmind_import.sql').read_text())
    return make,actors


def prepared(tmp_path,r,command_id=None,data=None):
    data=data or archive();key=command_id or uuid4();plan=xmind.parse(data)
    p=xmind.prepare(plan,data,'synthetic.xmind',tmp_path,r.scope.actor_user_id,r.scope.org_id,key)
    return key,p


def test_atomic_import_fixed_files_search_derivation_and_history(import_db,tmp_path):
    r=import_db[0]();key,p=prepared(tmp_path,r);before=r.canvas();result=r.import_xmind(key,p);cid=result['container_id']
    scene=r.canvas();nodes=[n for n in scene['nodes'] if str(n['container_id'])==cid];assert len(nodes)==5
    image=next(n for n in nodes if n['canvas_data'].get('image'))
    binding=image['canvas_data']['image'];version=r.file_version(image['id'],binding['binding_id'],1)
    assert read_version(tmp_path,version)==xmind.parse(archive()).resources['resources/pixel.png']
    assert len(r.search(cid,'先核对单号')['items'])==1
    assert len(r.search(cid,'长主题'*80)['items'])==1
    root=next(n for n in nodes if n['parent_id'] is None)
    for node in sorted(nodes,key=lambda n:next(t['depth'] for t in p['topics'] if t['title']==n['title']),reverse=True):
        assert asyncio.run(process(r,node['id'],fake))
    assert document(r,root['id'])['sections']
    leaf=next(n for n in nodes if '先核对' in n['title']);assert document(r,leaf['id'])['chunks']
    assert r.object(leaf['id'])['body']=='完整多行\n先核对单号，再确认收货。'
    head=r.canvas_history()['undo'];assert head['operation']=='import_xmind' and head['can_apply']
    assert r.import_xmind(key,p)['status']=='replayed'
    toggle(r);assert r.canvas()==before;assert r.search(cid,'先核对单号') is None
    assert r.import_xmind(key,p)['hidden']
    assert r.canvas()==before
    toggle(r,'redo');after=r.canvas();assert len(after['nodes'])==len(scene['nodes'])
    assert r.file_version(image['id'],binding['binding_id'],1)


def test_registration_failure_rolls_back_entire_project_and_can_retry(import_db,tmp_path):
    r=import_db[0]();key,p=prepared(tmp_path,r);bad=copy.deepcopy(p)
    bad['topics'][-1]['summary']=[9999];before=r.canvas()
    with pytest.raises(psycopg.Error):r.import_xmind(key,bad)
    assert r.canvas()==before
    assert r.import_xmind(key,p)['status']=='applied'
    changed=copy.deepcopy(p);changed['sha256']='0'*64
    with pytest.raises(psycopg.Error,match='KB_IDEMPOTENCY_PAYLOAD_CONFLICT'):r.import_xmind(key,changed)


def test_import_scope_and_permissions_on_sources_and_retry(import_db,tmp_path):
    make,actors=import_db;r=make();key,p=prepared(tmp_path,r);result=r.import_xmind(key,p);oid=result['object_id'];cid=result['container_id']
    assert make(1).object(oid) is None and make(0,1).object(oid) is None
    assert make(1).search(cid,'合成') is None
    source=r.files(oid)[0]
    assert make(1).file_version(oid,source['id'],1) is None
    # A different actor's same command creates a separate, private project.
    otherkey,otherp=prepared(tmp_path,make(1),key)
    assert make(1).import_xmind(otherkey,otherp)['container_id']!=cid
    with psycopg.connect(**make.admin_args) as c:
        c.execute("UPDATE org_members SET status='removed' WHERE org_id=%s AND user_id=%s",(r.scope.org_id,actors[0]))
    try:
        with pytest.raises(psycopg.Error,match='KB_ACCESS_DENIED'):r.import_xmind(key,p)
    finally:
        with psycopg.connect(**make.admin_args) as c:c.execute("UPDATE org_members SET status='active' WHERE org_id=%s AND user_id=%s",(r.scope.org_id,actors[0]))


def test_concurrent_retry_creates_once_and_reuses_immutable_bytes(import_db,tmp_path):
    make,_=import_db;r=make();key,p=prepared(tmp_path,r)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:make().import_xmind(key,p),range(2)))
    assert {v['status'] for v in results}=={'applied','replayed'}
    assert len({v['container_id'] for v in results})==1
    _,again=prepared(tmp_path,r,key);assert again==p


def test_imported_images_use_existing_media_cache_and_retrieval(import_db,tmp_path,monkeypatch):
    from services.knowledge_workspace import media
    r=import_db[0]();key,p=prepared(tmp_path,r);result=r.import_xmind(key,p)
    node=next(n for n in r.canvas()['nodes'] if str(n['container_id'])==result['container_id'] and n['canvas_data'].get('image'))
    calls=[]
    async def cached_extract(repo,oid,bid,revision):
        version=repo.file_version(oid,bid,revision)
        assert read_version(tmp_path,version).startswith(b'\x89PNG')
        calls.append((oid,bid,revision))
        return {'text':'合成图片中的审核步骤','coverage':'complete'}
    monkeypatch.setattr(media,'extract',cached_extract)
    asyncio.run(process(r,node['id'],fake));assert calls
    assert any('合成图片中的审核步骤' in c['text'] for c in document(r,node['id'])['chunks'])
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
    assert any(str(h['id'])==str(node['id']) for h in r.search(result['container_id'],'审核步骤')['items'])
    # A fixed-version derived generation is reused; no second image parse.
    with r.cursor() as c:c.execute('SELECT public.kb_enqueue_derivation(%s)',(node['id'],))
    asyncio.run(process(r,node['id'],fake));assert len(calls)==1


def test_import_undo_refuses_to_overwrite_other_editor(import_db,tmp_path):
    make,actors=import_db;r=make();key,p=prepared(tmp_path,r);result=r.import_xmind(key,p);cid=result['container_id']
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'editor','status':'active'}))
    make(1).command(command('update_title',{'object_id':result['object_id'],'expected_revision':1,'title':'其他编辑者的新文字'}))
    head=r.canvas_history()['undo'];assert head is None or head['history_id']!=str(key) or not head['can_apply']
    with pytest.raises(psycopg.Error,match='KB_HISTORY_CONFLICT'):r.command(command('undo_canvas',{'history_id':key}))
    assert r.object(result['object_id'])['title']=='其他编辑者的新文字'


def test_upload_route_applies_rollout_enterprise_and_file_service_guards(import_db,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from fastapi import Response,UploadFile
    from api.routes import knowledge_files as route
    from core.config import get_settings
    r=import_db[0]();ctx=SimpleNamespace(user_id=r.scope.actor_user_id,org_id=r.scope.org_id);db=SimpleNamespace(pool=r.pool)
    settings=get_settings();monkeypatch.setattr(settings,'file_workspace_root',str(tmp_path));monkeypatch.setattr(settings,'file_workspace_enabled',True)
    monkeypatch.setattr(route,'enabled',lambda _:True)
    target=create(r,title='现有空间');revision=next(c['revision'] for c in r.canvas()['containers'] if c['id']==target['container_id'])
    def run(name='test.xmind',data=None):
        return asyncio.run(route.import_xmind(ctx,db,Response(),uuid4(),UploadFile(filename=name,file=io.BytesIO(data or archive())),target['container_id'],revision))
    before=r.canvas()
    assert run('bad.zip').status_code==422 and r.canvas()==before
    ctx.org_id=None;assert run().status_code==403;ctx.org_id=r.scope.org_id
    monkeypatch.setattr(settings,'file_workspace_enabled',False);assert run().status_code==503
    monkeypatch.setattr(settings,'file_workspace_enabled',True)
    monkeypatch.setattr(route,'enabled',lambda _:False);assert run().status_code==404
    monkeypatch.setattr(route,'enabled',lambda _:True)
    assert run()['data']['report']['topics']==5


def test_multipart_endpoint_replays_without_reparsing_and_handles_errors(import_db,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import deps
    from api.routes import knowledge_files as route
    from core.config import get_settings
    r=import_db[0]();settings=get_settings()
    monkeypatch.setattr(settings,'file_workspace_root',str(tmp_path));monkeypatch.setattr(settings,'file_workspace_enabled',True)
    monkeypatch.setattr(route,'enabled',lambda _:True)
    app=FastAPI();app.include_router(route.router)
    app.dependency_overrides[deps.get_org_context]=lambda:SimpleNamespace(user_id=r.scope.actor_user_id,org_id=r.scope.org_id)
    app.dependency_overrides[deps.get_db]=lambda:SimpleNamespace(pool=r.pool)
    target=create(r,title='现有目标');revision=next(c['revision'] for c in r.canvas()['containers'] if c['id']==target['container_id'])
    key=str(uuid4());data=archive()
    with TestClient(app) as client:
        def post(body=data,command_id=key):return client.post('/knowledge/imports/xmind',data={'command_id':command_id,'container_id':str(target['container_id']),'expected_container_revision':revision},files={'file':('synthetic.xmind',body,'application/octet-stream')})
        first=post();assert first.status_code==200 and first.json()['data']['status']=='applied'
        assert first.headers['cache-control']=='no-store'
        monkeypatch.setattr(xmind,'parse',lambda _:pytest.fail('committed retry must not parse the archive again'))
        retry=post();assert retry.status_code==200 and retry.json()['data']['status']=='replayed'
        assert retry.json()['data']['container_id']==first.json()['data']['container_id']
        assert post(b'different').status_code==409
        monkeypatch.undo()
        monkeypatch.setattr(settings,'file_workspace_root',str(tmp_path));monkeypatch.setattr(settings,'file_workspace_enabled',True)
        monkeypatch.setattr(route,'enabled',lambda _:True)
        before=r.canvas();invalid=post(b'not zip',str(uuid4()))
        assert invalid.status_code==422 and invalid.json()['error']['code']=='KNOWLEDGE_INVALID_XMIND'
        assert r.canvas()==before


@pytest.mark.parametrize('limit',['expanded','members','content','topics'])
def test_archive_and_topic_capacities_are_checked_before_import(limit,monkeypatch):
    if limit=='expanded':monkeypatch.setattr(xmind,'MAX_EXPANDED',100);data=archive()
    elif limit=='content':monkeypatch.setattr(xmind,'MAX_CONTENT',10);data=archive()
    elif limit=='topics':monkeypatch.setattr(xmind,'MAX_TOPICS',2);data=archive()
    else:
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            for i in range(2001):z.writestr('part'+str(i),'')
        data=b.getvalue()
    with pytest.raises(AppException):xmind.parse(data)


def test_encrypted_flags_and_duplicate_json_fields_rejected():
    import struct
    data=bytearray(archive());central=data.index(b'PK\x01\x02')
    struct.pack_into('<H',data,6,struct.unpack_from('<H',data,6)[0]|1)
    struct.pack_into('<H',data,central+8,struct.unpack_from('<H',data,central+8)[0]|1)
    with pytest.raises(AppException):xmind.parse(bytes(data))
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:z.writestr('content.json','[{"rootTopic":{"id":"r","title":"first","title":"second"}}]')
    with pytest.raises(AppException):xmind.parse(output.getvalue())


def test_corrupt_image_fails_before_visible_registration(import_db,tmp_path):
    r=import_db[0]();before=r.canvas()
    data=archive(extra={'resources/pixel.png':b'\x89PNG\r\n\x1a\nbroken'})
    with pytest.raises(AppException):prepared(tmp_path,r,data=data)
    assert r.canvas()==before


def test_legacy_rich_notes_are_retained_as_source_text():
    root={'id':'r','title':'Topic','notes':{'realHTML':{'content':'<p>Full notes</p>'}}}
    plan=xmind.parse(archive([{'rootTopic':root}],{}))
    assert '<p>Full notes</p>' in plan.topics[0]['body']
    assert any('富文本' in w for w in plan.warnings)


def test_imported_text_is_readable_by_authorized_frozen_ai_snapshot(import_db,tmp_path):
    from tests.test_knowledge_chat_v3 import start,ref
    from core.exceptions import AppException
    make,_=import_db;r=make();key,p=prepared(tmp_path,r);result=r.import_xmind(key,p)
    root=r.object(result['object_id'])
    leaf=next(n for n in r.canvas()['nodes'] if str(n['container_id'])==result['container_id'] and '先核对' in n['title'])
    asyncio.run(process(r,leaf['id'],fake))
    run,_,_=start(make,r,[ref(root)]);handle=run.introduction()['roots'][0]['handle']
    hits=run.execute('search',{'root_handle':handle,'query':'先核对单号'})
    assert hits['items']
    evidence=run.execute('read',{'root_handle':handle,'item_handles':[i['handle'] for i in hits['items']],'context':True})
    assert any('先核对单号' in i['body'] for i in evidence['items'])
    with pytest.raises(AppException):start(make,make(1),[ref(root)])
    with pytest.raises(AppException):start(make,make(0,1),[ref(root)])


def test_import_tags_respect_existing_manual_limits_without_losing_text():
    plan=xmind.parse(archive([{'rootTopic':{'id':'r','title':'Topic','labels':['保留标签']}}],{}))
    assert plan.topics[0]['labels']==['保留标签']
    long='长'*51
    plan=xmind.parse(archive([{'rootTopic':{'id':'r','title':'Topic','labels':[long]}}],{}))
    assert long in plan.topics[0]['body'] and 'labels' not in plan.topics[0]


def test_inherited_foreground_remains_readable_on_source_topic_fill():
    from services.knowledge_workspace.xmind_presentation import presentation
    for fill,expected in [('#000000','#ffffff'),('#ed2814','#ffffff'),('#2634df','#ffffff'),('#ff8000','#202938')]:
        result=presentation({'style':{'properties':{'svg:fill':fill}}},{},1,'attached',lambda _:None)
        assert result['style']['color']==expected
    explicit=presentation({'style':{'properties':{'svg:fill':'#000000','fo:color':'#abcdef'}}},{},1,'attached',lambda _:None)
    assert explicit['style']['color']=='#abcdef'
