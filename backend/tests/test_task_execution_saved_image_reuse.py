"""Original pinned bodies and saved-source protocols, offline model/transport only."""
import asyncio,copy,hashlib,json,os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
import pytest
from services.task_execution.contracts import Artifact,PlanStep,WorkPlan,TaskError
from services.task_execution.reducer import revise_plan,register_artifacts,complete_step
from services.task_execution.methods import ImageMethodDocument,PROFILES
from services.task_execution.image_sources import SavedPromptDocument,SavedImageBrief,SourcePrompt,SOURCE_VALIDATOR,SOURCE_REQUIREMENT
from services.task_execution.image_binding import image_prompt,image_slots,bind_image_call
from services.task_execution.image_resources import bind_resources,BindImageResources
from services.task_execution.image_reuse import reuse,ReuseImageInput
from services.handlers.chat_image_preparation import prepare_input
from tests.test_task_execution_methods import document
from tests.test_task_execution_bootstrap import bootstrap
from tests.test_task_execution_model_bridge import setup_case,execute,call
from tests.test_task_execution_original_skills import original_runtime


@pytest.fixture(params=['repository', 'published'] if os.getenv('TASK_ORIGINAL_SKILLS_EXPORT') else ['repository'])
def original_source(request):
    if request.param == 'published':
        from tests.test_task_execution_original_skills import OriginalSource
        return OriginalSource()
    return RepositoryOriginalSource()


class RepositoryOriginalSource:
    """Repository bodies match the exact published body SHA; no production writes.

    This is not a reconstructed production package export: repository frontmatter
    is validated as v3, then the already-reviewed published identity is pinned.
    """
    def __init__(self):
        from services.skills.contracts import PackageCreate,PublishRevision
        from services.skills.storage import SkillStorage
        from services.skills.resolver import SkillCandidate
        self.values={};self.candidates=[];self.assets={}
        root=Path(__file__).resolve().parents[2]/'examples/skills/catalog/platform'
        for (package,key,revision,checksum),_profile in PROFILES.items():
            path=root/key/'v3/SKILL.md';raw=path.read_bytes()
            lines=raw.decode().splitlines(keepends=True);end=next(i for i in range(1,len(lines)) if lines[i].strip()=='---')
            body=''.join(lines[end+1:]);assert hashlib.sha256(body.encode()).hexdigest()==checksum
            skill=SkillStorage.validate_bytes(PackageCreate(skill_key=key,source='repository-exact-body',scope_kind='platform'),
                PublishRevision(revision='v3',content_sha256=hashlib.sha256(raw).hexdigest(),body_sha256=checksum),raw)
            self.values[key]=replace(skill,revision=revision)
            self.candidates.append(SkillCandidate(package_id=package,skill_key=key,revision=revision,
                package_org_id=None,package_user_id=None,assignment_org_id=None,scope_kind='platform',priority=0,
                description=skill.summary,catalog_metadata=skill.catalog_metadata))
            self.assets[key]={a.id:(path.parent/a.path).read_text() for a in skill.resources.assets}
    async def discover(self):return self.candidates
    async def session_bindings(self):return []
    async def load(self,candidate,*,restoring=False):return self.values[candidate.skill_key]
    async def load_assets(self,candidate,validated,ids):
        from services.skills.assets import verify_asset
        entries={a.id:a for a in validated.resources.assets}
        return {key:verify_asset(entries[key],self.assets[candidate.skill_key][key].encode()) for key in ids}


async def source_state(kind,monkeypatch):
    from services.task_execution.methods import attach_methods,validate_method_text
    boot,*_=bootstrap()
    s=boot.initialize({'action':'execute_ready' if kind=='plan' else 'execute_prompt','requirements':[{'id':'images','description':'一张原稿图','artifact_kind':'image','minimum':1}]},model_rounds_used=1)
    s=s.model_copy(update={'image_binding_version':3})
    s=revise_plan(s,WorkPlan(expected_revision=s.revision,steps=(
        PlanStep(id='source',title='读取原稿',requirement_ids=(SOURCE_REQUIREMENT,)),
        PlanStep(id='images',title='一张图',requirement_ids=('user.images',),depends_on=('source',),capability='platform.generate_image'))))
    if kind=='plan':
        from tests.test_task_execution_legacy_sources import plan
        from services.task_execution.legacy_sources import native_plan_document
        original=plan();original['items']=original['items'][:1];body=native_plan_document(original)
        reference=f"legacy-plan:{original['id']}:4"
        args={'plan_source':{'plan_id':original['id'],'revision':4,'item_id':original['items'][0]['item_id']}}
    else:
        message=str(uuid4());prompt='保留商品花纹，浅色桌面。\n负面提示词：不改变商品。'
        doc=SavedPromptDocument(image_plans=(SavedImageBrief(title='原提示词',prompt=prompt,aspect_ratio='3:4',plan_item_id='saved-original',
            source_prompt=SourcePrompt(message_id=message,content_index=0,sha256=hashlib.sha256(prompt.encode()).hexdigest())),))
        body=doc.model_dump_json();reference='legacy-message:'+message;original=None
        args={'mode':'text_to_image','prompt':prompt,'aspect_ratio':'3:4','plan_item_id':'saved-original','source_prompt':doc.image_plans[0].source_prompt.model_dump(mode='json')}
    a=Artifact(id='original-source',kind='text',reference=reference,sha256=hashlib.sha256(body.encode()).hexdigest(),inline_text=body,
        source_task_id=s.root_task_id,delivery_task_id=s.root_task_id,source_call_id='source',step_id='source',validations=('artifact.exists',SOURCE_VALIDATOR))
    s=register_artifacts(s,[a]);s=complete_step(s,'source',[a.id])
    # Actual host receipt schema is covered by PG/native source checks. The
    # semantic observer below is a local deterministic substitute, not a model.
    monkeypatch.setattr('services.task_execution.plan_facts.review_proof',lambda state,artifact:{'sources':{'resolved':[]}})
    return s,a,args,original


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['plan','prompt','regeneration'])
@pytest.mark.parametrize('fault',[None,'changed','denied','different_variant'])
async def test_saved_entry_reuses_exact_published_child_without_business_submission(monkeypatch,kind,fault):
    s,a,args,original=await source_state('plan' if kind=='plan' else 'prompt',monkeypatch)
    resolved=[{'workspace_path':'original.png','content_sha256':'a'*64,'file_version':[1,2,3,4],'role':'product'}] if kind=='plan' else []
    normalized={'mode':'image_to_image','prompt':original['items'][0]['request_text'],'aspect_ratio':'3:4'} if kind=='plan' else copy.deepcopy(args)
    if kind!='plan':
        bound,_=await bind_resources(s,BindImageResources(expected_revision=s.revision,planning_artifact_id=a.id,image_item_index=1,
            mode='text_to_image',references=[]),resolver=AsyncMock(return_value=[]),owner_task_id=s.root_task_id,call_id='bind')
        s=register_artifacts(s,[bound])
    if kind=='regeneration':args.update(source_task_id=str(uuid4()),variant_id='stable-retry');normalized.update(source_task_id=args['source_task_id'],variant_id=args['variant_id'])
    proof={**args['plan_source'],'request_text_sha256':original['items'][0]['request_text_sha256']} if kind=='plan' else None
    resolver=AsyncMock(return_value=[]);resolver.prepare_request=AsyncMock(return_value=(normalized,resolved,proof))
    row={'task_id':str(uuid4()),'message_id':str(uuid4()),'content_index':0,'part':{'type':'image','url':'https://example.invalid/saved.png'},
        'part_sha256':'b'*64,'request':{**normalized,'request_hash':'c'*64,'references':resolved,'origin':{'tool_call_id':'prior',**({'plan_source':proof} if proof else {})}}}
    if fault=='changed':row['request']['prompt']='different exact prompt'
    if fault=='denied':resolver.prepare_request.side_effect=PermissionError('revoked')
    if fault=='different_variant':args['variant_id']='new-variant'
    reader=SimpleNamespace(reusable_images=AsyncMock(return_value=[row]))
    if fault:
        with pytest.raises(TaskError):await reuse(s,ReuseImageInput(expected_revision=s.revision,arguments=args),reader=reader,resolver=resolver,owner_task_id=s.root_task_id)
        return
    artifact,part=await reuse(s,ReuseImageInput(expected_revision=s.revision,arguments=args),reader=reader,resolver=resolver,owner_task_id=s.root_task_id)
    assert not s.operations and image_slots(register_artifacts(s,[artifact]))[0]['status']=='completed'
    assert part==row['part'];resolver.prepare_request.assert_awaited_once()
    if kind=='regeneration':
        args.pop('variant_id')
        with pytest.raises(TaskError,match='REGENERATION_REQUEST'):
            await reuse(s,ReuseImageInput(expected_revision=s.revision,arguments=args),reader=reader,resolver=resolver,owner_task_id=s.root_task_id)


def native_plan_fixture():
    from tests.test_task_execution_legacy_sources import plan
    from services.agent.image.ecommerce_planner.contracts import source_id
    p=plan();ref={'message_id':str(uuid4()),'content_index':0,'workspace_path':'original.png','content_sha256':'a'*64,'file_version':[1,2,3,4],'role':'product'}
    ref['source_id']=source_id(ref)
    for item in p['items']:item['references']=[{'message_id':ref['message_id'],'content_index':0,'role':'product','source_id':ref['source_id']}]
    p.update(user_id='actor',org_id=None,conversation_id='conv',input_snapshot={'resolved_references':[ref]})
    return p,ref


@pytest.mark.parametrize('fault',[None,'size','hash','revision','reference','scope','workflow'])
def test_shared_native_saved_plan_expansion_preserves_original_checks(fault):
    p,ref=native_plan_fixture();item=p['items'][0]
    args={'plan_source':{'plan_id':p['id'],'revision':4,'item_id':item['item_id']}}
    db=Mock();db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value=SimpleNamespace(data=p)
    native=Mock(owner=SimpleNamespace(user_id='actor',org_id=None,conversation_id='conv'))
    native.normalize_legacy.side_effect=lambda args:args;native.resolve.return_value=[copy.deepcopy(ref)]
    native.size_context.return_value=({'aspect_ratio':'1:1'} if fault=='size' else {},None)
    if fault=='hash':item['request_text_sha256']='0'*64
    if fault=='revision':p['plan_revision']=5
    if fault=='reference':native.resolve.return_value[0]['content_sha256']='0'*64
    if fault=='scope':p['user_id']='other'
    workflow=SimpleNamespace(kind='main_images',plan_id=str(uuid4())) if fault=='workflow' else None
    if fault:
        with pytest.raises((ValueError,PermissionError)):prepare_input(native,args,scoped=db,workflow=workflow,settings=SimpleNamespace(chat_image_transparent_enabled=False))
        return
    normalized,refs,size,proof=prepare_input(native,args,scoped=db,workflow=None,settings=SimpleNamespace(chat_image_transparent_enabled=False))
    assert normalized['prompt']==item['request_text'] and normalized['aspect_ratio']=='3:4' and proof['item_id']==item['item_id']
    assert refs[0]['content_sha256']==ref['content_sha256'] and 'size_requirement' not in normalized

async def approve(runtime,prepared,handler,sink,artifact_id,step_id):
    from services.task_execution.plan_facts import before_complete,apply_review,sections
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.task_execution.reducer import digest
    sources={'references':[],'resolved':[],'origins':[],'texts':[{'id':'user:current','text':'保留商品外观，按原稿执行五张图'}]}
    sources['sha256']=digest(sources)
    runtime.task_plan_source_provider=AsyncMock(return_value=(sources,[]))
    s=runtime.task_execution_state;a=next(a for a in s.artifacts if a.id==artifact_id)
    await before_complete(runtime,s,step_id,[artifact_id],force=True)
    fields=sections(a)
    observation={'reviewed_sections':list(fields),'claims':[{'section':key,'claim':text,'status':'supported',
        'evidence':[{'source_id':'user:current','kind':'user_text','quote':sources['texts'][0]['text']}]} for key,text in fields.items()]}
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=json.dumps(observation),has_tool_calls=False,
        model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1,sink=sink)
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{'expected_revision':runtime.task_execution_state.revision,
        'step_id':step_id,'artifact_ids':[artifact_id]},'complete-'+step_id)],[])
    assert step_id in {r.step_id for r in runtime.task_execution_state.results}


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['reference-image-prompts','ecommerce-image-prompts'])
async def test_original_pinned_body_plan_handoff_compression_and_five_reused_images(key, original_source):
    from services.skills.selection import SkillSelection
    from services.task_execution.methods import attach_methods,blocked_tool_names
    from services.task_execution.requirement_changes import RevisionReview,RevisionProposal,candidate,requirements_fingerprint
    from services.task_execution.reducer import finish
    from services.tools.catalog import build_capability_catalog
    source=original_source;skill=source.values[key];selection=SkillSelection(skill_id=key,revision=skill.revision)
    skills=original_runtime(source);await skills.initialize(selection=selection);assert (await skills.activate_manual(selection))['ok']
    boot,*_=bootstrap()
    initial=attach_methods(boot.initialize({'action':'plan','applicable_method_ids':[key],'requirements':[
        {'id':'prompts','description':'五条完整提示词','artifact_kind':'text','item_count':5}]},model_rounds_used=1),skills)
    initial=initial.model_copy(update={'image_binding_version':3})
    initial=revise_plan(initial,WorkPlan(expected_revision=initial.revision,steps=(PlanStep(id='planning',title='五条完整规划',
        requirement_ids=('user.prompts',*initial.method_bindings[0].requirement_ids)),)))
    runtime,prepared,handler,sink=setup_case(initial=initial);runtime.skill_runtime=skills
    prepared.messages.insert(0,{'role':'system','content':'保留原会话记忆，不删除'})
    await execute(runtime,prepared,handler,sink,[call('save_task_text',{'expected_revision':initial.revision,'step_id':'planning','text':document()},'save-original')],[])
    a=runtime.task_execution_state.artifacts[-1]
    await approve(runtime,prepared,handler,sink,a.id,'planning')
    old=runtime.task_execution_state;old_skills=runtime.skill_runtime
    assert (key in old_skills.resident)==(key=='reference-image-prompts')
    restored=original_runtime(source,task_state=old,turn_id=old_skills.turn_id);await restored.initialize(old_skills.checkpoint())
    assert restored.manual_selection==selection and restored.checkpoint()==old_skills.checkpoint()
    text='按刚才原稿直接执行五张图'
    event={'event_id':str(uuid4()),'owner_task_id':str(old.root_task_id),'message':text,'message_sha256':hashlib.sha256(text.encode()).hexdigest()}
    book=RevisionReview(root_task_id=old.root_task_id,base_requirements_sha256=requirements_fingerprint(old),events=[event],
        pending=RevisionProposal(relation='amend',evidence=[text],proposal={'action':'execute_ready','requirements':[
            {'id':'images','description':'按原稿交付五张图','artifact_kind':'image','minimum':5,'evidence':[text]}]}))
    revised=candidate(old,book,restored)
    revised=revise_plan(revised,WorkPlan(expected_revision=revised.revision,steps=(
        PlanStep(id='r1.source',title='读取原规划',requirement_ids=('r1.host.image_source',)),
        PlanStep(id='r1.images',title='交付五张已有图',requirement_ids=('r1.user.images',),depends_on=('r1.source',),capability='platform.generate_image'))))
    runtime.task_execution_state=revised;runtime.skill_runtime=restored
    runtime.task_artifact_reader=SimpleNamespace(list_text=AsyncMock(return_value=[]),reusable_images=AsyncMock())
    # Original full text may have been compressed. The source is loaded from
    # persisted artifacts, not recovered by a summary/model rewriting it.
    prepared.messages[:]=[prepared.messages[0],{'role':'user','content':text}]
    await execute(runtime,prepared,handler,sink,[call('reuse_task_text',{'expected_revision':revised.revision,'step_id':'r1.source',
        'source_logical_task_id':str(old.root_task_id),'source_artifact_id':a.id},'reuse-original')],[])
    imported=runtime.task_execution_state.artifacts[-1]
    assert imported.inline_text==a.inline_text and SOURCE_VALIDATOR in imported.validations
    assert imported.reference==f'task-source:{old.root_task_id}:{a.id}'
    assert 'generate_image' in blocked_tool_names(runtime.task_execution_state,build_capability_catalog())
    await approve(runtime,prepared,handler,sink,imported.id,'r1.source')
    assert not blocked_tool_names(runtime.task_execution_state,build_capability_catalog())
    resolver=AsyncMock(return_value=[]);runtime.task_image_resource_resolver=resolver
    doc=ImageMethodDocument.model_validate_json(imported.inline_text)
    rows=[];ids=[]
    for index,brief in enumerate(doc.image_plans,1):
        await execute(runtime,prepared,handler,sink,[call('bind_task_image_sources',{'expected_revision':runtime.task_execution_state.revision,
            'planning_artifact_id':imported.id,'image_item_index':index,'mode':'text_to_image','references':[]},'bind-'+str(index))],[])
        row={'task_id':str(uuid4()),'message_id':str(uuid4()),'content_index':0,'part':{'type':'image','url':f'https://example.invalid/{index}.png'},
            'part_sha256':hashlib.sha256(str(index).encode()).hexdigest(),'request':{'request_hash':'c'*64,'mode':'text_to_image','prompt':image_prompt(brief),
            'aspect_ratio':brief.aspect_ratio,'references':[],'origin':{'tool_call_id':'original-'+str(index)}}}
        rows.append(row);runtime.task_artifact_reader.reusable_images.return_value=rows
        await execute(runtime,prepared,handler,sink,[call('generate_image',{'mode':'text_to_image','prompt':image_prompt(brief),
            'aspect_ratio':brief.aspect_ratio},'image-'+str(index))],[])
        assert json.loads(prepared.messages[-1]['content'])['reused']
        ids.append(runtime.task_execution_state.artifacts[-1].id)
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{'expected_revision':runtime.task_execution_state.revision,
        'step_id':'r1.images','artifact_ids':ids},'complete-images')],[])
    final=finish(runtime.task_execution_state)
    assert final.status=='completed' and not final.operations and len(ids)==5
    assert a in final.artifacts and final.requirements_history[0].requirements==old.requirements
    assert prepared.messages[0]['content']=='保留原会话记忆，不删除'
    assert runtime.skill_runtime.manual_selection==selection
    handler._execute_tool_calls.assert_not_called()
    cp=runtime.skill_runtime.checkpoint();again=original_runtime(source,task_state=final,turn_id=runtime.skill_runtime.turn_id)
    await again.initialize(cp);assert again.checkpoint()==cp


from tests.test_chat_image_sources import context
from tests.test_chat_image_reference_continuity import source


@pytest.mark.asyncio
async def test_actual_native_prepare_callback_checks_original_files_again(monkeypatch,context):
    from tests.test_task_execution_image_resources import bound_case,native
    from services.task_execution.image_resources import create_resolver
    from services.tools import ToolContext
    from services.tools.spec import thaw
    from PIL import Image
    state,command,proof,_=await bound_case(monkeypatch,context)
    row,owner,files,current=context
    current['content']=[{'type':'text','text':'确认，按3:4生成'}]
    owner.db.set_table_data('messages',[row,current])
    task=str(uuid4())
    owner.db.set_table_data('tasks',[{'id':task,'user_id':owner.user_id,'org_id':None,'conversation_id':owner.conversation_id,
        'input_message_id':current['id'],'base_context_revision':1,'request_params':{}}])
    monkeypatch.setattr('services.file_executor.FileExecutor',lambda *_,**__:files)
    monkeypatch.setattr('core.db_scope.ScopedDatabaseClient',lambda db,scope:db)
    handler=SimpleNamespace(db=owner.db)
    request=SimpleNamespace(task_id=task,conversation_id=owner.conversation_id)
    scope=ToolContext(actor_user_id=owner.user_id,workspace_owner_id=owner.user_id,org_id=None,context_scope='user',
        personal_context_allowed=True,agent_domain='general',permission_mode='auto',execution_mode='interactive')
    resolve=create_resolver(handler,request,scope)
    args=thaw(native(references=list(command.references),size_requirement={'mode':'explicit'}).arguments)
    normalized,refs,plan=await resolve.prepare_request(args,state,'reuse-check')
    assert plan is None and normalized['prompt']==args['prompt'] and [r['workspace_path'] for r in refs]==['B.png','A.png']
    Image.new('RGB',(4,4),'green').save(files.resolve_safe_path('B.png'))
    with pytest.raises((ValueError,TaskError)):await resolve.prepare_request(args,state,'reuse-check')


def test_imported_saved_prompt_can_be_reused_without_rewriting_or_old_review_grant():
    from services.task_execution.image_sources import reused_source
    from services.task_execution.contracts import Requirement
    from tests.test_task_execution_reducer import state
    s=state();s=s.model_copy(update={'intent_action':'execute_prompt','requirements':(Requirement(id=SOURCE_REQUIREMENT,
        source_message_id=s.source_message_ids[0],description='原稿',artifact_kind='text',validator=SOURCE_VALIDATOR),
        Requirement(id='images',source_message_id=s.source_message_ids[0],description='图',artifact_kind='image')),
        'steps':(PlanStep(id='source',title='原稿',requirement_ids=(SOURCE_REQUIREMENT,)),)})
    message=str(uuid4());prompt='原完整提示词'
    body=SavedPromptDocument(image_plans=(SavedImageBrief(title='原稿',prompt=prompt,aspect_ratio='3:4',plan_item_id='saved-1',
        source_prompt=SourcePrompt(message_id=message,content_index=0,sha256=hashlib.sha256(prompt.encode()).hexdigest())),)).model_dump_json()
    a=Artifact(id='old-prompt',kind='text',reference='legacy-message:'+message,sha256=hashlib.sha256(body.encode()).hexdigest(),
        inline_text=body,source_task_id=s.root_task_id,source_call_id='old',step_id='old',validations=('artifact.exists',SOURCE_VALIDATOR,'image.facts.reviewed'))
    ref,validations=reused_source(s,'source',a,s.root_task_id)
    assert ref==a.reference and validations==(SOURCE_VALIDATOR,) and a.inline_text==body
