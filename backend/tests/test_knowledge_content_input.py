"""Atomic input edits and retries on an isolated loopback database."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock
import psycopg
import pytest
from tests.test_knowledge_derivation import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db)
from services.knowledge_workspace.content_input import ContentInput, prepare


@pytest.fixture(scope='module')
def content_db(derived_db):
    make, actors = derived_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute((Path(__file__).resolve().parents[1]/'migrations/336_knowledge_content_input.sql').read_text())
    return make, actors


def content_payload(r, node, **changes):
    cid = node['container_id']
    return {'container_id':cid,'expected_container_revision':rev(r,cid),
            'object_id':node['id'],'expected_revision':node['revision'],
            'title':'不允许用此标题改名','body':'原有条件保留，时间改为昨天。',
            'position':{'x':20,'y':40},'children':[],**changes}


def test_amend_preserves_title_versions_and_supports_undo_redo(content_db):
    r=content_db[0]();n=create(r,title='用户命名')
    r.command(command('save_document',{'object_id':n['id'],'expected_revision':1,'body':'原有条件保留，时间为最近30天。'}))
    n=r.object(n['id']);cmd=command('apply_content',content_payload(r,n))
    first=r.command(cmd);assert r.command(cmd)['status']=='replayed'
    assert r.object(n['id'])['title']=='用户命名'
    assert r.object(n['id'])['body']=='原有条件保留，时间改为昨天。'
    assert r.version(n['id'],n['revision'])['body']=='原有条件保留，时间为最近30天。'
    _,undo=toggle(r)
    assert r.object(n['id'])['body']=='原有条件保留，时间为最近30天。'
    assert r.command(undo)['status']=='replayed'
    toggle(r,'redo');assert r.object(n['id'])['body']=='原有条件保留，时间改为昨天。'
    assert first['object_id']==str(n['id'])


def test_first_input_creates_tree_atomically_and_replay_does_not_duplicate(content_db):
    r=content_db[0]();root=create(r)
    p=content_payload(r,root,object_id=None,expected_revision=None,parent_id=root['id'],title='售后处理',input_text='用户原始输入：整理漏发补寄和退货',body='漏发补寄与退货说明',children=[
        {'title':'漏发补寄','body':'先核对订单，再补寄。','position':{'x':400,'y':0}},
        {'title':'退货','body':'食品拆封不适用。','position':{'x':400,'y':220}}])
    cmd=command('apply_content',p);result=r.command(cmd);oid=result['object_id']
    assert r.version(oid,1)['body']=='用户原始输入：整理漏发补寄和退货'
    assert r.object(oid)['body']=='漏发补寄与退货说明'
    children=[n for n in r.canvas()['nodes'] if str(n['parent_id'])==oid]
    assert len(children)==2
    assert {r.object(n['id'])['body'] for n in children}=={'先核对订单，再补寄。','食品拆封不适用。'}
    assert r.command(cmd)['object_id']==oid
    assert len([n for n in r.canvas()['nodes'] if str(n['parent_id'])==oid])==2
    toggle(r);assert not any(str(n['id'])==oid for n in r.canvas()['nodes'])
    toggle(r,'redo');assert r.object(oid)['body']=='漏发补寄与退货说明'


def test_conflict_permission_and_failed_child_cannot_partially_save(content_db):
    r=content_db[0]();n=create(r);original=r.object(n['id'])
    p=content_payload(r,n)
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):
        content_db[0](1).command(command('apply_content',p))
    with pytest.raises(Exception,match='KB_VERSION_CONFLICT'):
        r.command(command('apply_content',{**p,'expected_revision':99}))
    with pytest.raises(Exception):
        # Bypass the Pydantic boundary to verify database rollback after a bad child.
        with r.cursor() as c:
            from psycopg.types.json import Jsonb
            from uuid import uuid4
            raw=command('apply_content',p).payload.model_dump(mode='json')
            raw['children']=[{'title':'','body':'错误节点','position':{'x':400,'y':0}}]
            c.execute('SELECT public.kb_apply_content(%s,%s)',(uuid4(),Jsonb(raw)))
    assert r.object(n['id'])==original


def test_prepare_is_readonly_preserves_existing_title_and_checks_acl(content_db,monkeypatch):
    from services.knowledge_workspace import content_input
    from core.config import get_settings
    r=content_db[0]();n=create(r,title='原有标题');before=r.object(n['id'])
    monkeypatch.setattr(get_settings(),'dashscope_api_key','synthetic-unit-test-key')
    response=type('Response',(),{'raise_for_status':lambda self:None,'json':lambda self:{'choices':[{'message':{'content':json.dumps({'title':'模型试图改名','body':'整理后的正文','children':[]})},'finish_reason':'stop'}]}})()
    client=type('Client',(),{'post':AsyncMock(return_value=response)})()
    monkeypatch.setattr(content_input._client,'get',AsyncMock(return_value=client))
    request=ContentInput(container_id=n['container_id'],object_id=n['id'],expected_revision=1,text='补充说明')
    plan=asyncio.run(prepare(r,request));assert plan['title']=='原有标题';assert r.object(n['id'])==before
    with pytest.raises(PermissionError):asyncio.run(prepare(content_db[0](1),request))
    assert client.post.await_count==1
