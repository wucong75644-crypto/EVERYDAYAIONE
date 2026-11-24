"""User intent fixes: no extra approval/model, keep native execution contracts."""
from types import SimpleNamespace
import pytest
from schemas.message import ImagePart, TextPart
from services.handlers.chat import content_tools
from services.handlers.image_size_requirements import resolve_size_requirement,user_size_intent

@pytest.mark.parametrize('text',[
    '帮我写几个可以生图的提示词，文字就行。',
    '帮我写几套可以生图的可复制提示词，文字就行。',
    '帮我写几个可用于生成图片的高质量英文提示词。',
    '帮我策划3套京东电商主图方案和可以生图的可复制提示词，先只写文字，不生成图片。',
    '帮我写几套“生成图片”的提示词，不要实际执行。',
    '帮我写几套「生成图片」的提示词，不要实际执行。',
    '只写几套生图用的提示词',
    '帮我写几个出图的提示词，文字就行。',
    '参考这张产品图，帮我写3套可以生图的完整提示词，文字就行，不要实际生成。',
    '参考这张产品图，帮我写3套可以生图的详细提示词，文字就行。',
    '补充这张图片供你观察波点排列，只作设计分析，不作为后续生图输入；三套方案都设为浅米灰色，仍然只写文字，不要生成图片。',
])
def test_prompt_text_and_quoted_actions_are_not_image_execution(text):
    q=SimpleNamespace(content=[TextPart(text=text)])
    assert not content_tools.image_execution_request(q)
    assert content_tools.image_writing_only_request(q)

@pytest.mark.parametrize('text',[
    '先生成一张图片，再写提示词',
    '请生成一张图片，再把提示词整理出来。',
    '先写提示词，然后生成一张图片。',
    '生成一张图片，标题写“生成图片”。',
    '请生成图片的同时写提示词。',
    '写好方案然后生成图片再写提示词。',
    '写好方案和生成一张图片。',
])
def test_real_generation_in_another_clause_still_executes(text):
    q=SimpleNamespace(content=[TextPart(text=text)])
    assert content_tools.image_execution_request(q)
    assert not content_tools.image_writing_only_request(q)

@pytest.mark.parametrize('text,ratio,tier,requested',[
    ('生成约1920×1080的图片，接近即可','16:9','2K',(1920,1080)),
    ('做790*790左右，按接近尺寸匹配','1:1','1K',(790,790)),
    ('做一张1600×2400，允许近似','2:3','2K',(1600,2400)),
    ('输出3840×2160左右','16:9','4K',(3840,2160)),
    ('约1920×1080，但是用1K档位','16:9','1K',(1920,1080)),
])
def test_approximate_pixels_select_supported_specs_not_an_exact_output_contract(text,ratio,tier,requested):
    args,target=resolve_size_requirement({'mode':'text_to_image','prompt':'商品摄影',
        'size_requirement':{'mode':'explicit','width':requested[0],'height':requested[1]}},[],intent=user_size_intent(text))
    assert (args['aspect_ratio'],args['resolution'])==(ratio,tier)
    assert (target['requested_width'],target['requested_height'])==requested
    assert target['approximate'] is True
    assert 'width' not in target and 'height' not in target
    assert 'size_requirement' not in args

@pytest.mark.parametrize('text',[
    '必须精确1920×1080，不允许近似',
    '做一张1920×1080，背景颜色大约浅蓝色',
    '精确生成1920×1080',
])
def test_exact_pixels_and_approximate_background_cannot_bypass_existing_capability_check(text):
    with pytest.raises(ValueError,match='EXACT_SIZE_UNSUPPORTED'):
        resolve_size_requirement({'mode':'text_to_image','prompt':'test'},[],intent=user_size_intent(text))

@pytest.mark.asyncio
@pytest.mark.parametrize('text',[
    '帮我写几个可以生图的提示词，文字就行。',
    '帮我写几套“生成图片”的提示词，不要实际执行。',
])
async def test_hallucinated_image_tool_is_not_dispatched_for_text_only_variants(text):
    from dataclasses import replace
    from tests.test_model_directed_loop import case,execute
    from tests.test_task_execution_model_bridge import call
    r,p,h,q=case();p.content_controls_available=True
    q=replace(q,content=[TextPart(text=text)])
    await execute(r,p,h,q,[call('generate_image',{'prompt':'must not purchase','mode':'text_to_image'},'not-authorized')])
    h._execute_tool_calls.assert_not_awaited()
    assert any('本轮用户只要求文字' in str(m.get('content','')) for m in p.messages)


@pytest.mark.asyncio
async def test_server_planner_does_not_accept_model_authored_text_as_a_ready_plan():
    import json
    from services.task_execution import content_objects as co
    from tests.test_content_objects import case,document
    from tests.test_model_directed_loop import execute
    from tests.test_task_execution_model_bridge import call
    r,p,h,q=case();p.content_controls_available=True
    p.ecom_workflow=SimpleNamespace(value=SimpleNamespace(kind='main_images'),server_selected=lambda:True)
    assert not any(t['function']['name']==co.SAVE for t in content_tools.model_tools([co.SCHEMA],server_image_plan=True))
    await execute(r,p,h,q,[call(co.SAVE,{'document':document()},'not-a-native-plan')])
    assert not co.catalog(r.task_execution_state)
    feedback=json.loads(p.messages[-1]['content'])
    assert feedback['code']=='ECOM_PLAN_SERVER_AUTHOR_REQUIRED'
    assert 'plan_ecommerce_images' in feedback['message']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('prefix',['选项','套系','方向','创意','版本'])
@pytest.mark.parametrize('style',['heading','bold'])
def test_observed_multilingual_quoted_prompts_keep_whole_sections_and_numbered_references(prefix,style):
    from services.task_execution.content_groups import index_document
    sections=['> **中文描述：** 完整产品提示词，保持全部限制。\n>\n> **English Prompt:** Full prompt, preserve all constraints.',
        '**中文提示词：**\n第二个完整方案。\n\n**English prompt:** Second full prompt.\n\n**建议参数：** 不得丢弃本段限制。']
    titles=[f'{prefix}一：第一套',f'{prefix}二：第二套']
    headings=[f'## {title}' if style=='heading' else f'**{title}**' for title in titles]
    text=f'说明原文\n\n{headings[0]}\n\n{sections[0]}\n\n{headings[1]}\n\n{sections[1]}'
    indexed=index_document(text,allow_plain=True)
    assert indexed is not None and len(indexed.document.items)==2
    assert [item.body for item in indexed.document.items]==sections
    assert index_document(text,allow_plain=False) is None

@pytest.mark.parametrize('quote',[("'","'"),('‘','’'),('`','`')])
def test_alternative_quotation_styles_are_text_not_actions(quote):
    q=SimpleNamespace(content=[TextPart(text=f'帮我写几个{quote[0]}生成图片{quote[1]}的提示词，文字就行。')])
    assert not content_tools.image_execution_request(q)
    assert content_tools.image_writing_only_request(q)


def test_approximate_size_matching_a_saved_plan_keeps_its_exact_text_and_native_reference():
    from copy import deepcopy
    import hashlib
    from uuid import uuid4
    from services.handlers.chat_image_preparation import prepare_input
    plan_id,item_id=map(str,(uuid4(),uuid4()))
    prompt='既有完整方案原文，不重新策划。'
    ref={'asset_id':'source','content_sha256':'a'*64,'file_version':[1,2,3,4],'workspace_path':'source.png','role':'product'}
    plan={'id':plan_id,'user_id':'owner','org_id':None,'conversation_id':'conv','plan_revision':1,'status':'ready',
        'items':[{'item_id':item_id,'request_text':prompt,'request_text_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'references':[ref],'aspect_ratio':'16:9'}],
        'input_snapshot':{'resolved_references':[{**ref,'source_id':'source'}]},
        'target_size':{'mode':'explicit','aspect_ratio':'16:9','resolution':'2K'}}
    class Query:
        def __getattr__(self,name):return lambda *a,**k:self
        def execute(self):return SimpleNamespace(data=deepcopy(plan))
    resolver=SimpleNamespace(owner=SimpleNamespace(user_id='owner',org_id=None,conversation_id='conv'),
        normalize_legacy=lambda a:deepcopy(a),locate_source_prompt=lambda _:None,
        resolve=lambda _: [deepcopy(ref)],verify=lambda *a,**k:None,
        size_context=lambda **k:(user_size_intent('约1920×1080，接近即可'),{}))
    args,proof_refs,target,proof=prepare_input(resolver,{'plan_source':{'plan_id':plan_id,'revision':1,'item_id':item_id}},
        scoped=SimpleNamespace(table=lambda _:Query()),workflow=None,
        settings=SimpleNamespace(chat_image_transparent_enabled=False),entry_checked=True)
    assert args['prompt']==prompt and (args['aspect_ratio'],args['resolution'])==('16:9','2K')
    assert proof['item_id']==item_id and proof_refs[0]['content_sha256']==ref['content_sha256']
    assert target['approximate'] and target['requested_width']==1920 and 'width' not in target

@pytest.mark.parametrize('user_text,exact',[
    ('只把刚生成的蓝色图背景改成灰色，其他不变',False),
    ('只把背景改灰色，必须精确1920×1080',True),
])
def test_fresh_edit_inherits_native_canvas_instead_of_model_invented_pixels(user_text,exact):
    from copy import deepcopy
    from services.handlers.chat_image_preparation import prepare_input
    from services.handlers.chat_input_handoff import digest
    previous={'aspect_ratio':'16:9','resolution':'2K','mode':'explicit'}
    args={'mode':'image_to_image','prompt':'只改背景。输出尺寸约1920×1080，接近即可。',
        'references':[{'message_id':'prior','content_index':0,'role':'底图'}],
        'size_requirement':{'mode':'explicit','width':1920,'height':1080}}
    source={'content_object':{'arguments_sha256':digest(args)}}
    owner=SimpleNamespace(task_id='current')
    ref={'source':'generated','message_id':'prior','content_index':0,'workspace_path':'previous.png'}
    resolver=SimpleNamespace(owner=owner,normalize_legacy=lambda a:deepcopy(a),
        locate_source_prompt=lambda _:source,complete_source_prompt=lambda s,p:(p,s),
        content_image_item=lambda _:(SimpleNamespace(source_call_id='inline-save-edit',source_task_id='current'),SimpleNamespace(options={})),
        resolve=lambda _:[ref],verify=lambda *a,**k:None,
        size_context=lambda **k:(user_size_intent(user_text),{}),selected_image_size=lambda _:previous)
    options={'scoped':None,'workflow':None,'settings':SimpleNamespace(chat_image_transparent_enabled=False),'entry_checked':True}
    if exact:
        with pytest.raises(ValueError,match='EXACT_SIZE_UNSUPPORTED'):prepare_input(resolver,args,**options)
    else:
        normalized,refs,target,_=prepare_input(resolver,args,**options)
        assert (normalized['aspect_ratio'],normalized['resolution'])==('16:9','2K')
        assert normalized['prompt']==args['prompt'] and refs==[ref]
        assert 'width' not in target and 'height' not in target


@pytest.mark.asyncio
@pytest.mark.parametrize('fenced',[False,True,'mixed'])
@pytest.mark.parametrize('explicit_size',[False,True])
async def test_optional_size_advice_does_not_reject_visible_prose_registration_or_next_turn_source(fenced,explicit_size):
    from dataclasses import replace
    from services.adapters.types import StreamChunk
    from services.task_execution import content_objects as co
    from services.handlers.chat.outcome_builder import build_content_parts
    from tests.test_content_objects import case, run_content_loop, ref
    r,p,h,q=case()
    q=replace(q,content=[TextPart(text='参考这张图帮我写几个可以生图的提示词，文字就行。'+('每套输出16:9、2K。' if explicit_size else '')),
        ImagePart(name='原图.png',workspace_path='uploads/original.png')],
        context_anchor=SimpleNamespace(input_message_id=str(r.task_bootstrap.input_message_id)))
    text=('**选项一：商品摄影**\n> **中文：** 保持商品原有结构，白色背景。\n'
          '> **English:** Preserve the product, white background.\n\n'
          '**选项二：桌面场景**\n> **中文：** 保持商品原有结构，桌面背景。\n'
          '> **English:** Preserve the product, desktop background.\n\n'
          '**使用建议：**\n* 可选 `--ar 3:4` (竖图) 或 `--ar 1:1` (方图)。')
    if fenced:
        text=('### 方案一：产品摄影\n**方案内容：** 保留原结构。\n'
              '**生图提示词：**\n```text\n白色产品，完整正向要求。\n```\n'
              '**负面提示词：**\n```text\n不得变形，禁止新增结构。\n```\n\n'
              '### 方案二：桌面场景\n**方案内容：** 保留原文字。\n'
              '**生图提示词：**\n```text\n白色产品放在桌面，完整正向要求。\n```\n'
              '**负面提示词：**\n```text\n不得改字，禁止新增赠品。\n```')
        if fenced == 'mixed':
            text=text.replace('```text\n白色产品放在桌面，完整正向要求。\n```','白色产品放在桌面，完整正向要求。')
            text=text.replace('```text\n不得改字，禁止新增赠品。\n```','不得改字，禁止新增赠品。')
    if explicit_size:
        text=text.replace('保留原结构。','保留原结构。模型建议输出1:1、1K。')
    rounds=[]
    async def stream(**kwargs):
        rounds.append(kwargs)
        yield StreamChunk(content=text)
        yield StreamChunk(finish_reason='stop')
    _,blocks,totals=await run_content_loop(r,p,h,q,stream)
    part,=build_content_parts(blocks,fallback_text=totals.text)
    assert part.text==text and not part.content_incomplete
    row,=co.catalog(r.task_execution_state)
    assert len(row['items'])==2 and len(rounds)==1
    args,_=co.compile_input(r.task_execution_state,'generate_image',{},ref(row,2))
    assert args['prompt']==text.split('### 方案二：桌面场景\n' if fenced else '**选项二：桌面场景**\n')[1]
    if explicit_size:
        assert (args['aspect_ratio'],args['resolution'])==('16:9','2K')
    else:
        assert 'aspect_ratio' not in args
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('text', [
    '参考这个产品，帮我想3个新款，把提示词写出来，我先看看。',
    '这几个方案整理出来，文字就行。',
    '提示词写一下。',
])
def test_object_before_verb_is_a_text_authoring_request(text):
    q = SimpleNamespace(content=[TextPart(text=text)])
    assert content_tools.authored_text_request(q)
    assert content_tools.image_writing_only_request(q)
    assert not content_tools.image_execution_request(q)
