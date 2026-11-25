"""Index real ordinary authoring shapes without model formatting calls."""
import pytest
from services.task_execution.content_groups import index_document


def test_chinese_numbered_prompts_preserve_full_visible_sections_and_constraints():
    text=('设计建议\n\n### 方案一：简约\n**设计思路：** 保留主体。\n**提示词参考：**\n'
        '> 修改背景为浅蓝。\n禁止新增商品页数。\n\n'
        '### 方案二：温暖\n**设计思路：** 原装订不变。\n**提示词参考：**\n'
        '> 修改背景为米灰。\n\n---\n使用图片中原有结构，不改尺寸。')
    assert index_document(text) is None  # Method-specific boundary rules stay strict.
    result=index_document(text,allow_plain=True)
    assert result and len(result.document.items)==2
    assert '禁止新增商品页数' in result.document.items[0].body
    assert '使用图片中原有结构，不改尺寸' in result.document.items[1].body
    rebuilt=result.gaps[0]
    for i,item in enumerate(result.document.items):
        assert item.body==text[slice(*result.ranges[i])]
        rebuilt+=item.body+result.gaps[i+1]
    assert rebuilt==text


def test_ordinary_items_keep_fenced_and_plain_text_with_post_fence_constraints():
    sections=['```text\n白色商品摄影。\n```\n不得改动商品文字。',
              '灰色商品摄影。\n不得改变装订。']
    text='### 方案一：白色\n'+sections[0]+'\n\n### 方案二：灰色\n'+sections[1]
    indexed=index_document(text,allow_plain=True)
    assert indexed and [item.body for item in indexed.document.items]==sections
    assert index_document(text) is None
    assert all(text[slice(*span)]==item.body for span,item in zip(indexed.ranges,indexed.document.items))


def test_explicit_author_size_outranks_model_body_and_structured_options():
    from services.task_execution.content_groups import bind_group
    from services.task_execution.content_objects import ContentDocument,ContentItem
    document=ContentDocument(title='原稿',items=(ContentItem(title='第一套',body='模型建议1:1、1K。',
        options={'aspect_ratio':'3:4','resolution':'4K'}),))
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    saved=bind_group(document,[],'每套输出16:9、2K。',
        semantic_context=ImageRequestContext(size={'aspect_ratio':'16:9','resolution':'2K'}))
    assert saved.items[0].options=={'aspect_ratio':'16:9','resolution':'2K'}
    assert saved.items[0].body==document.items[0].body


@pytest.mark.parametrize('headings',[
    ('方案一：甲','方案三：乙'),('方案一：甲','方案一：乙'),
    ('第一章','第二章'),('方案二：甲','方案三：乙'),
])
def test_ambiguous_or_nonconsecutive_chinese_labels_are_not_silently_numbered(headings):
    assert index_document('\n\n'.join('### '+heading+'\n完整正文' for heading in headings),allow_plain=True) is None


def test_only_fresh_image_writing_uses_host_registration_without_model_save():
    from types import SimpleNamespace
    from schemas.message import TextPart
    from services.handlers.chat.content_tools import prefers_plain_image_text
    from tests.test_model_directed_loop import case
    runtime,_,_,_=case()
    from tests.semantic_fixtures import semantic_request
    request=semantic_request(SimpleNamespace(content=[TextPart(text='帮我写5套图片提示词，先不要生图')]),'write')
    assert prefers_plain_image_text(request,runtime)
    assert not prefers_plain_image_text(request,runtime,saved_source_available=True)
    assert not prefers_plain_image_text(request,runtime,server_image_plan=True)
    request=semantic_request(SimpleNamespace(content=[TextPart(text='帮我把第一套生成图片')]),'execute_saved')
    assert not prefers_plain_image_text(request,runtime)


def test_consumed_additional_material_does_not_transfer_writing_back_to_model_save():
    from types import SimpleNamespace
    from schemas.message import TextPart
    from services.handlers.chat.content_tools import prefers_plain_image_text
    from tests.test_model_directed_loop import case
    runtime,_,_,_=case()
    from tests.semantic_fixtures import semantic_request
    request=semantic_request(SimpleNamespace(content=[TextPart(text='帮我写3套图片提示词，先不要生成图片')]),'write')
    runtime.task_user_changes=SimpleNamespace(items=(SimpleNamespace(owner_task_id=runtime.task_id,
        message='补充一张观察照片，背景浅灰色。',delivered=True),))
    assert prefers_plain_image_text(request,runtime)
    runtime.task_user_changes.items+=(SimpleNamespace(owner_task_id=runtime.task_id,message='现在生成第一套图片',delivered=True),)
    runtime.workflow_projection={'intent':'execute_saved','request_context':{'media':{'tool':'generate_image','wait_for':[1]}}}
    assert not prefers_plain_image_text(request,runtime)


@pytest.mark.parametrize('text',['写一个图片提示词，先不要生图','参考这张图写提示词，不要生图'])
def test_single_or_unspecified_writing_keeps_original_structured_save_contract(text):
    from types import SimpleNamespace
    from schemas.message import TextPart
    from services.handlers.chat.content_tools import prefers_plain_image_text
    from tests.test_model_directed_loop import case
    runtime,_,_,_=case()
    assert not prefers_plain_image_text(SimpleNamespace(content=[TextPart(text=text)]),runtime)


def test_explicit_prompt_fences_keep_presentation_and_summary_out_of_execution_without_losing_visible_bytes():
    from services.task_execution.content_objects import render
    text=('## 1. 奶白款\n设计说明：保持封面。\n```prompt\n奶白封面，禁止修改原有印刷。\n```\n\n---\n\n'
          '## 2. 浅灰款\n设计说明：只换背景。\n```prompt\n背景浅灰，商品保持不变。\n```\n\n'
          '## 总结\n第一套温暖，第二套简约。要生成哪一套？')
    result=index_document(text,allow_plain=True)
    assert result and [item.body for item in result.document.items]==[
        '奶白封面，禁止修改原有印刷。','背景浅灰，商品保持不变。']
    assert render(result.document,result.gaps)==text
    assert all(text[slice(*span)]==item.body for span,item in zip(result.ranges,result.document.items))
    assert '设计说明' not in ''.join(item.body for item in result.document.items)
    assert '总结' not in ''.join(item.body for item in result.document.items)


def test_declared_prompt_fences_keep_arbitrary_presentation_outside_execution():
    text='简介\n\n'+ '\n\n'.join(
        f'### 方案{i}：方向{i}\n解释{i}\n\n```prompt\n完整执行稿{i}，禁止变形。\n```'
        for i in range(1,4))+'\n\n---\n\n**比较说明**\n这些是给用户看的介绍，包括对限制的解释。'
    indexed=index_document(text,allow_plain=True,fenced_prompts=True)
    assert [item.body for item in indexed.document.items]==[f'完整执行稿{i}，禁止变形。' for i in range(1,4)]
    rebuilt=indexed.gaps[0]
    for item,gap in zip(indexed.document.items,indexed.gaps[1:],strict=True):rebuilt+=item.body+gap
    assert rebuilt==text
    # Existing untyped documents retain their original conservative tail handling.
    legacy=index_document(text,allow_plain=True)
    assert '**比较说明**' in legacy.document.items[-1].body
