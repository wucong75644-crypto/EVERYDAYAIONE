"""Source selection, exact bytes and recovery using actual temporary originals."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from services.agent.file_id import compute_fid
from services.handlers.chat_image_request import ChatImageInputResolver, freeze_image_request, verify_frozen_request
from services.handlers.chat_context.image_sources import image_sources, legacy_catalog, discovered_image_sources
from tests.test_chat_image_reference_continuity import source

@pytest.fixture
def context(source):
    row,owner,files=source
    row['message_kind']='conversation'
    current={**row,'id':'confirmation','context_revision':None,'content':[{'type':'text','text':'确认，生成两张'}]}
    owner.db.set_table_data('messages',[row,current])
    return row,owner,files,current

def resolver(context,**kwargs):
    _,owner,files,_=context
    return ChatImageInputResolver(owner,base_revision=1,input_message_id='confirmation',files=files,require_known_sources=True,**kwargs)

def test_file_id_selects_only_b_original_and_keeps_explicit_reference_order(context):
    _,owner,files,_=context
    selected=resolver(context).resolve([{'file_id':compute_fid(None,'B.png'),'role':'product'},
                                        {'file_id':compute_fid(None,'A.png'),'role':'style'}])
    assert [r['workspace_path'] for r in selected]==['B.png','A.png']
    assert [r['source_content_index'] for r in selected]==[3,1]
    assert selected[0]['content_sha256']==hashlib.sha256(files.resolve_safe_path('B.png').read_bytes()).hexdigest()
    # A fresh Worker/ retry resolver reads the selected source evidence.
    ChatImageInputResolver(owner,base_revision=1,input_message_id='confirmation',files=files).verify(selected)

def test_unrelated_workspace_file_is_not_a_selected_chat_reference(context):
    from PIL import Image
    _,_,files,_=context
    Image.new('RGB',(4,4),'green').save(files.resolve_safe_path('unseen.png'))
    with pytest.raises(ValueError,match='RESOURCE_NOT_FOUND'):
        resolver(context).resolve([{'file_id':compute_fid(None,'unseen.png'),'role':'product'}])

def test_actual_search_result_and_authenticated_checkpoint_can_restore_identity(context):
    from services.tools.resource_access import ResourceSelections
    from PIL import Image
    _,owner,files,current=context
    Image.new('RGB',(4,4),'green').save(files.resolve_safe_path('search.png'))
    selections=ResourceSelections();owner._tool_runtime=SimpleNamespace(resource_selections=selections)
    ref=resolver(context).files.reference('search.png')
    selections.record_image_result(owner,'  resource_ref: '+ref+'\n')
    selected=resolver(context).resolve([{'file_id':compute_fid(None,'search.png'),'role':'product'}])
    assert selected[0]['resource_ref']==ref and selected[0]['source']=='file_search'
    ChatImageInputResolver(owner,base_revision=1,input_message_id=current['id'],files=files).verify(selected)
    restored=ResourceSelections()
    restored.restore(owner,[{'type':'tool_step','tool_name':'file_search','status':'completed','input':'{}','output':'  resource_ref: '+ref}])
    assert restored.image_references==selections.image_references

def test_legacy_evidence_survives_parent_restart_without_new_history(context):
    row,owner,files,_=context
    row['context_revision']=None
    frozen=deepcopy(row)
    selected=resolver(context,legacy_sources=[frozen]).resolve([{'file_id':compute_fid(None,'B.png'),'role':'product'}])
    assert selected[0]['legacy_source']==frozen
    owner.db.set_table_data('messages',[row,{**row,'id':'future','content':[{'type':'image','workspace_path':'A.png'}]}])
    worker=ChatImageInputResolver(owner,base_revision=1,input_message_id='confirmation',files=files)
    worker.verify(selected)
    with pytest.raises(PermissionError,match='IMAGE_SOURCE_REVISION_DENIED'):
        worker.resolve([{'message_id':'future','content_index':0,'role':'product'}])

def test_changed_unrelated_legacy_image_does_not_poison_another_source(context):
    row,owner,_,current=context
    row['context_revision']=None
    obsolete={**deepcopy(row),'id':'obsolete','content':[{'type':'image','workspace_path':'A.png'}]}
    frozen=[deepcopy(row),deepcopy(obsolete)]
    obsolete['content']=[{'type':'text','text':'deleted image'}]
    owner.db.set_table_data('messages',[row,obsolete,current])
    assert resolver(context,legacy_sources=frozen).resolve([{'file_id':compute_fid(None,'B.png'),'role':'product'}])[0]['workspace_path']=='B.png'
    with pytest.raises(ValueError,match='IMAGE_SOURCE_MESSAGE_CHANGED'):
        resolver(context,legacy_sources=frozen).resolve([{'message_id':'obsolete','content_index':0,'role':'style'}])

@pytest.mark.parametrize('changed',[{'workspace_path':'A.png'}, {'url':'replacement'}, {'failed':True}])
def test_legacy_message_mutation_refuses_recovery_not_new_generation(context,changed):
    row,owner,files,_=context;row['context_revision']=None;frozen=deepcopy(row)
    selected=resolver(context,legacy_sources=[frozen]).resolve([{'message_id':row['id'],'content_index':3,'role':'product'}])
    row['content'][3].update(changed)
    with pytest.raises(ValueError,match='IMAGE_SOURCE_MESSAGE_CHANGED'):
        ChatImageInputResolver(owner,base_revision=1,input_message_id='confirmation',files=files).verify(selected)

def test_short_id_collision_requires_exact_message_location(context,monkeypatch):
    monkeypatch.setattr('services.agent.file_id.compute_fid',lambda *_:'fid_00000000')
    with pytest.raises(ValueError,match='IMAGE_REFERENCE_AMBIGUOUS'):
        resolver(context).resolve([{'file_id':'fid_00000000','role':'product'}])
    assert resolver(context).resolve([{'message_id':'source-message','content_index':3,'role':'product'}])[0]['workspace_path']=='B.png'

def test_same_original_in_multiple_messages_retains_occurrences(context):
    row,owner,_,current=context
    duplicate={**deepcopy(row),'id':'duplicate','content':[row['content'][3]]}
    owner.db.set_table_data('messages',[row,duplicate,current])
    selected=resolver(context).resolve([{'file_id':compute_fid(None,'B.png'),'role':'product'}])
    assert {r['message_id'] for r in selected[0]['occurrences']}=={'source-message','duplicate'}

def test_invalid_quote_does_not_hide_a_verified_occurrence_of_same_original(context):
    row,owner,_,current=context
    invalid={**deepcopy(row),'id':'invalid-quote','content':[{'type':'image','workspace_path':'B.png',
        'source_message_id':row['id'],'source_content_index':1}]}
    owner.db.set_table_data('messages',[invalid,row,current])
    selected=resolver(context).resolve([{'file_id':compute_fid(None,'B.png'),'role':'product'}])
    assert selected[0]['source_message_id']==row['id']
    assert selected[0]['occurrences']==[{'message_id':row['id'],'content_index':3}]

@pytest.mark.parametrize('path',['A.png','B.png'])
def test_client_quote_is_verified_against_original_location(context,path):
    row,owner,_,current=context
    current['content']=[{'type':'image','workspace_path':path,'source_message_id':row['id'],'source_content_index':3}]
    if path=='A.png':
        with pytest.raises(ValueError,match='IMAGE_QUOTED_SOURCE_CHANGED'):
            resolver(context).resolve([{'message_id':current['id'],'content_index':0,'role':'product'}])
    else:
        selected=resolver(context).resolve([{'message_id':current['id'],'content_index':0,'role':'product'}])
        assert selected[0]['source']=='quoted' and selected[0]['quoted_message_id']==row['id']
    displayed=discovered_image_sources({**current,'conversation_id':owner.conversation_id},owner.db,org_id=None,owner_id=owner.user_id)
    assert displayed[0]['available']==(path=='B.png')

def test_current_visual_metadata_retains_original_indices_and_unavailable_reason(context):
    row,_,_,_=context
    sources=image_sources(row,None,[3])
    assert [item['content_index'] for item in sources]==[1,3]
    assert [item['visually_present'] for item in sources]==[False,True]
    assert all(len(item['reference'])==2 for item in sources)
    row['content'].append({'type':'image','url':'https://third-party.test/image'})
    assert image_sources(row)[-1]['unavailable_reason']=='IMAGE_ORIGINAL_UNAVAILABLE'

def test_url_only_original_reuses_registered_asset_and_file_id(context,monkeypatch):
    row,owner,files,current=context
    monkeypatch.setattr('services.assets.asset_identity.configured_asset_hosts',lambda:frozenset({'media.test'}))
    row['content']=[{'type':'image','name':'B.png','asset_id':'real-asset','url':'https://media.test/old/B?signature=obsolete'}]
    owner.db.set_table_data('user_assets',[{'id':'real-asset','org_id':None,'storage_owner_key':owner.user_id,
        'storage_scope':'user','storage_provider':'oss','storage_key':'old/B','media_type':'image','status':'ready',
        'workspace_path':'B.png','content_sha256':hashlib.sha256(files.resolve_safe_path('B.png').read_bytes()).hexdigest()}])
    displayed=discovered_image_sources(row,owner.db,org_id=None,owner_id=owner.user_id)
    assert displayed[0]['available'] and displayed[0]['asset_id']=='real-asset'
    selected=resolver(context).resolve([{'file_id':displayed[0]['file_id'],'role':'product'}])
    assert selected[0]['workspace_path']=='B.png' and selected[0]['source_asset_id']=='real-asset'
    ChatImageInputResolver(owner,base_revision=1,input_message_id=current['id'],files=files).verify(selected)
    selected_message=resolver(context).resolve([{'message_id':row['id'],'content_index':0,'role':'product'}])
    ChatImageInputResolver(owner,base_revision=1,input_message_id=current['id'],files=files).verify(selected_message)

@pytest.mark.asyncio
async def test_two_single_accepts_freeze_exact_prompt_and_b_reference(context,monkeypatch,tmp_path):
    from core.config import Settings
    from services.handlers.image_handler import ImageHandler
    from services.tools.dispatcher import _dispatch_call_id
    row,owner,files,current=context
    owner.__dict__.update(task_id='parent',image_execution_token='token',context_scope='user',execution_mode='interactive',cancellation_event=None)
    owner.db.set_table_data('tasks',[{'id':'parent','user_id':owner.user_id,'org_id':None,'conversation_id':owner.conversation_id,
        'turn_id':'turn','input_message_id':current['id'],'base_context_revision':1,'request_params':{}}])
    monkeypatch.setattr('core.config.get_settings',lambda:Settings(_env_file=None,database_url='postgresql://invalid/test',jwt_secret_key='isolated-test-key',
        file_workspace_root=str(tmp_path),chat_image_async_enabled=True))
    monkeypatch.setattr('core.db_scope.ScopedDatabaseClient',lambda db,scope:db)
    captures=[]
    def rpc(name,args):
        captures.append(deepcopy(args['p_snapshot']))
        return SimpleNamespace(execute=lambda:SimpleNamespace(data={'outcome':'accepted','task_id':str(len(captures)),'message_id':'pending','submission_state':'queued'}))
    owner.db.rpc=Mock(side_effect=rpc)
    for n in range(2):
        token=_dispatch_call_id.set(f'call-{n}')
        try:
            receipt=await ImageHandler(owner.db).accept_chat_image(owner,{'mode':'image_to_image','prompt':f'  精确提示词 {n}\n',
                'references':[{'file_id':compute_fid(None,'B.png'),'role':'product'}]})
            assert receipt['task_id']==str(n+1)
        finally:_dispatch_call_id.reset(token)
    assert len(captures)==2 and {s['num_images'] for s in captures}=={1}
    assert [s['prompt'] for s in captures]==['  精确提示词 0\n','  精确提示词 1\n']
    assert all(s['references'][0]['workspace_path']=='B.png' for s in captures)
    row['content'][1]['workspace_path']='changed_after_accept.png'
    for frozen in captures:verify_frozen_request(frozen)
    assert captures[0]['references'][0]['workspace_path']=='B.png'


def exact_reader(context, legacy=False):
    from services.agent.conversation_tool_mixin import ConversationToolMixin
    row,owner,_,current=context
    owner.__dict__.update(task_id='parent',personal_context_allowed=True)
    prompt='  保留空白的完整提示词\n第二行，不能用摘要替代。  '
    row['content'].append({'type':'text','text':prompt})
    parent={'id':'parent','user_id':owner.user_id,'org_id':None,'conversation_id':owner.conversation_id,
        'base_context_revision':1,'input_message_id':current['id'],'request_params':{}}
    if legacy:
        row['context_revision']=None
        parent['request_params']['_image_sources_v1']={'version':1,'task_id':parent['id'],'user_id':owner.user_id,
            'org_id':None,'conversation_id':owner.conversation_id,'base_revision':1,
            'input_message_id':current['id'],'messages':[deepcopy(row)]}
    owner.db.set_table_data('tasks',[parent])
    return row,owner,prompt,lambda args:ConversationToolMixin._exact_image_history(owner,args)


@pytest.mark.parametrize('legacy',[False,True])
def test_exact_reader_returns_full_prompt_hash_and_existing_file_identity(context,legacy):
    import json
    row,_,prompt,read=exact_reader(context,legacy)
    result=json.loads(read({'message_ids':[row['id']],'text_exact':prompt}))
    assert result['selected_prompt']['prompt']==prompt
    assert result['selected_prompt']['source_prompt']=={'message_id':row['id'],'content_index':4,
        'sha256':hashlib.sha256(prompt.encode()).hexdigest()}
    images=[p for p in result['messages'][0]['parts'] if p['type']=='image']
    assert [p['file_id'] for p in images]==[compute_fid(None,'A.png'),compute_fid(None,'B.png')]
    assert [p['reference']['content_index'] for p in images]==[1,3]


def test_exact_reader_never_expands_frozen_legacy_catalog(context):
    row,owner,_,read=exact_reader(context,True)
    owner.db.set_table_data('messages',[row,{**deepcopy(row),'id':'after-claim'}])
    with pytest.raises(PermissionError,match='IMAGE_SOURCE_REVISION_DENIED'):
        read({'message_ids':['after-claim']})
    row['content'][1]['workspace_path']='replaced.png'
    with pytest.raises(ValueError,match='IMAGE_SOURCE_MESSAGE_CHANGED'):
        read({'message_ids':[row['id']]})


@pytest.fixture
def cross_conversation_quote(context):
    row, owner, files, current = context
    row['conversation_id'] = 'previous-conversation'
    current['content'] = [{'type':'image', 'workspace_path':'B.png',
        'source_message_id':row['id'], 'source_content_index':3}]
    owner.db.set_table_data('messages', [row, current])
    owner.db.set_table_data('conversations', [{'id':row['conversation_id'],
        'user_id':owner.user_id, 'org_id':owner.org_id, 'scope_type':'user'}])
    return context


def test_existing_cross_conversation_quote_keeps_current_locator_and_original_canvas(
    cross_conversation_quote, monkeypatch,
):
    from PIL import Image
    from services.handlers.image_size_requirements import resolve_size_requirement, user_size_intent, size_preflight_notice
    row, owner, files, current = cross_conversation_quote
    Image.new('RGB', (1080,1080), 'red').save(files.resolve_safe_path('B.png'))
    monkeypatch.setattr('core.config.get_settings', lambda:SimpleNamespace(file_workspace_root=str(files._workspace_base)))
    displayed = discovered_image_sources(current,owner.db,org_id=owner.org_id,owner_id=owner.user_id)
    assert displayed[0]['available']
    assert displayed[0]['reference']=={'message_id':current['id'],'content_index':0}
    assert displayed[0]['quoted_message_id']==row['id']
    assert displayed[0]['canvas']['aspect_ratio']=='1:1'
    assert '\"aspect_ratio\": \"1:1\"' in size_preflight_notice('参考图片写提示词',displayed)
    selected = resolver(cross_conversation_quote).resolve([displayed[0]['reference']|{'role':'product'}])
    args,target = resolve_size_requirement({'mode':'image_to_image','prompt':'笔记本产品比例3:4，保留原图画布',
        'aspect_ratio':'3:4','resolution':'1K'},selected,intent=user_size_intent('随机选三个提示词生成图片'))
    assert args['aspect_ratio']=='1:1' and target['original_width']==target['original_height']==1080
    frozen=freeze_image_request(args,selected,origin={'parent_task_id':'parent'},max_credits=300,max_requests=15,size_requirement=target)
    verify_frozen_request(frozen)
    ChatImageInputResolver(owner,base_revision=1,input_message_id=current['id'],files=files).verify(selected)
    args, target=resolve_size_requirement({'mode':'image_to_image','prompt':'产品比例3:4','aspect_ratio':'1:1'},
        selected,intent=user_size_intent('输出改成3:4'))
    assert args['aspect_ratio']=='3:4' and target['mode']=='explicit'


@pytest.mark.parametrize('changes',[
    {'user_id':'another-user'}, {'org_id':'another-org'}, {'scope_type':'channel'},
])
def test_existing_quote_cannot_cross_owner_org_or_channel(cross_conversation_quote, changes):
    row,owner,files,current=cross_conversation_quote
    owner.db.set_table_data('conversations',[{'id':row['conversation_id'],'user_id':owner.user_id,
        'org_id':owner.org_id,'scope_type':'user',**changes}])
    with pytest.raises(PermissionError,match='IMAGE_QUOTED_SOURCE_DENIED'):
        resolver(cross_conversation_quote).resolve([{'message_id':current['id'],'content_index':0,'role':'product'}])


def test_cross_conversation_quote_still_rejects_changed_origin_and_unquoted_selector(cross_conversation_quote):
    row,owner,files,current=cross_conversation_quote
    with pytest.raises(PermissionError,match='IMAGE_SOURCE_MESSAGE_DENIED'):
        resolver(cross_conversation_quote).resolve([{'message_id':row['id'],'content_index':3,'role':'product'}])
    current['content'][0]['workspace_path']='A.png'
    with pytest.raises(ValueError,match='IMAGE_QUOTED_SOURCE_CHANGED'):
        resolver(cross_conversation_quote).resolve([{'message_id':current['id'],'content_index':0,'role':'product'}])


@pytest.mark.parametrize('changed_task',[False,True])
def test_cross_conversation_generated_quote_checks_origin_task_conversation(cross_conversation_quote,changed_task):
    row,owner,files,current=cross_conversation_quote
    row['role']='assistant'
    current['content'][0]['source_task_id']='origin-image-task'
    owner.db.set_table_data('tasks',[{'id':'origin-image-task','user_id':owner.user_id,'org_id':owner.org_id,
        'conversation_id':'unrelated-conversation' if changed_task else row['conversation_id'],
        'type':'image','status':'completed','assistant_message_id':row['id']}])
    if changed_task:
        with pytest.raises(ValueError,match='IMAGE_QUOTED_SOURCE_CHANGED'):
            resolver(cross_conversation_quote).resolve([{'message_id':current['id'],'content_index':0,'role':'product'}])
    else:
        selected=resolver(cross_conversation_quote).resolve([{'message_id':current['id'],'content_index':0,'role':'product'}])
        assert selected[0]['quoted_task_id']=='origin-image-task'


@pytest.mark.parametrize('selector', ['message', 'file_id'])
def test_deleted_quote_origin_uses_the_selected_workspace_original(context, monkeypatch, selector):
    row, owner, files, current = context
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(file_workspace_root=str(files._workspace_base)))
    current['content'] = [
        {'type': 'image', 'workspace_path': name, 'source_message_id': 'deleted-origin',
         'source_content_index': index, 'source_task_id': 'deleted-task'}
        for name, index in [('B.png', 3), ('A.png', 1)]
    ]
    owner.db.set_table_data('messages', [current])
    displayed = discovered_image_sources(current, owner.db, org_id=owner.org_id, owner_id=owner.user_id)
    assert all(item['available'] for item in displayed)
    assert [item['file_id'] for item in displayed] == [compute_fid(owner.org_id, name) for name in ['B.png', 'A.png']]
    refs = [({'message_id': current['id'], 'content_index': i} if selector == 'message'
             else {'file_id': item['file_id']}) | {'role': 'product'} for i, item in enumerate(displayed)]
    selected = resolver(context).resolve(refs)
    assert [item['workspace_path'] for item in selected] == ['B.png', 'A.png']
    assert all('quoted_message_id' not in item and 'quoted_task_id' not in item for item in selected)
    assert selected[0]['content_sha256'] == hashlib.sha256(files.resolve_safe_path('B.png').read_bytes()).hexdigest()
    resolver(context).verify(selected)
    current['content'] = [{'type': 'image', 'workspace_path': name} for name in ['B.png', 'A.png']]
    clean = resolver(context).resolve(refs)
    assert [(item['workspace_path'], item['content_sha256']) for item in selected] == [(item['workspace_path'], item['content_sha256']) for item in clean]


@pytest.mark.parametrize('path', ['missing.png', '../outside.png'])
def test_deleted_origin_does_not_authorize_missing_or_outside_workspace_files(context, path):
    _, owner, _, current = context
    current['content'] = [{'type': 'image', 'workspace_path': path, 'source_message_id': 'deleted-origin', 'source_content_index': 0}]
    owner.db.set_table_data('messages', [current])
    with pytest.raises((PermissionError, ValueError, OSError)):
        resolver(context).resolve([{'message_id': current['id'], 'content_index': 0, 'role': 'product'}])


@pytest.mark.parametrize('failure', ['permission', 'asset', 'corrupt'])
def test_deleted_origin_preserves_workspace_permission_asset_and_image_checks(context, failure):
    from services.tools.resource_access import ResourceAccessBoundary
    _, owner, files, current = context
    current['content'] = [{'type': 'image', 'workspace_path': 'B.png',
                           'source_message_id': 'deleted-origin', 'source_content_index': 0}]
    owner.db.set_table_data('messages', [current])
    if failure == 'permission':
        owner.resource_access_boundary = ResourceAccessBoundary((), 'denied', False)
    elif failure == 'asset':
        current['content'][0]['asset_id'] = 'foreign-asset'
        owner.db.set_table_data('user_assets', [{'id': 'foreign-asset', 'org_id': owner.org_id,
            'storage_owner_key': 'other-user', 'storage_scope': 'user', 'workspace_path': 'B.png',
            'status': 'ready', 'media_type': 'image'}])
    else:
        files.resolve_safe_path('B.png').write_bytes(b'not an image')
    with pytest.raises((PermissionError, ValueError, OSError)):
        resolver(context).resolve([{'message_id': current['id'], 'content_index': 0, 'role': 'product'}])
