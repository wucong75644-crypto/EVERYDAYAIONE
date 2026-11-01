"""Opt-in real-provider coverage tests using the two original published Skills.

Requires TASK_COVERAGE_LIVE=1, TASK_ORIGINAL_SKILLS_EXPORT,
TASK_ORIGINAL_INPUTS_ROOT and an executable TASK_ORIGINAL_MODEL_PROXY.
The proxy accepts {messages,tools} JSON and returns {content,calls,usage} JSON.
It must call the real configured provider; it never dispatches business tools.
Credentials, original user inputs and exported Skill bodies stay outside Git.
This is coverage-judgment evidence, not image-provider or UI acceptance.
"""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from services.task_execution.bootstrap import StartTaskInput
from services.task_execution.requirement_review import RequirementReview,review_messages,review_decision
from tests.test_task_execution_original_skills import OriginalSource

pytestmark=pytest.mark.skipif(os.getenv('TASK_COVERAGE_LIVE')!='1',reason='opt-in original-provider coverage acceptance')


@pytest.mark.asyncio
@pytest.mark.parametrize('case,key,expected',[
    ('original','reference-image-prompts','covered'),
    ('original','ecommerce-image-prompts','covered'),
    ('dropped_images','ecommerce-image-prompts','missing'),
    ('dropped_images','reference-image-prompts','missing'),
    ('prompt_only','ecommerce-image-prompts','covered'),
])
async def test_real_provider_reviews_final_deliverables_without_treating_phase_as_whole_task(case,key,expected):
    source=OriginalSource();skill=source.values[key]
    root=Path(os.environ['TASK_ORIGINAL_INPUTS_ROOT'])
    assert root.is_absolute() and all((root/f'original-product-{i}.png').is_file() for i in (1,2))
    goal=(root/'original-request.txt').read_text()
    if case=='prompt_only':goal='只要五条提示词，不要生成图片'
    text=case in {'dropped_images','prompt_only'}
    proposal=StartTaskInput.model_validate({'action':'plan','applicable_method_ids':[], 'requirements':[{
        'id':'delivery','description':'五条详情图提示词' if text else '五张拼多多详情图，桌面场景突出原商品',
        'artifact_kind':'text' if text else 'image','minimum':1 if text else 5,'item_count':5 if text else None,
        'evidence':['五条提示词'] if case=='prompt_only' else ['帮我生成五张详情页的图']}]})
    record=RequirementReview(version=2,root_task_id=uuid4(),actor_user_id=uuid4(),conversation_id=uuid4(),source_message_id=uuid4(),
        original_goal=goal,has_user_image_references=True,pending=proposal,selected_methods=({
            'skill_id':key,'revision':skill.revision,'body_sha256':skill.body_sha256,'name':skill.catalog_metadata.name,
            'sources':['user'],'phase_resident':False},))
    proxy=Path(os.environ['TASK_ORIGINAL_MODEL_PROXY'])
    assert proxy.is_absolute() and proxy.is_file()
    process=await asyncio.create_subprocess_exec(str(proxy),stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(90):
            output,_errors=await process.communicate(json.dumps({'messages':review_messages(record),'tools':[]},ensure_ascii=False).encode())
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
    assert process.returncode==0,'original provider proxy failed; inspect private proxy diagnostics'
    response=json.loads(output)
    assert response['calls']==[]
    _record,verdict=review_decision(record,response['content'],has_tool_calls=bool(response['calls']),model_round=1)
    assert verdict.coverage==expected,verdict
    assert expected!='covered' or not verdict.issues
