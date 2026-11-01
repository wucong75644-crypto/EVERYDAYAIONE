"""Opt-in original published inputs; never publish/create a replacement Skill.

TASK_ORIGINAL_SKILLS_EXPORT points at the private read-only export produced by
SkillStorage.validate on the server. This verifies original package integration,
not provider output quality or completed image delivery.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from services.skills.assets import verify_asset
from services.skills.contracts import PublishRevision,SkillPackage
from services.skills.resolver import SkillCandidate
from services.skills.runtime import SkillRuntime
from services.skills.selection import SkillSelection
from services.skills.storage import SkillStorage
from services.task_execution import methods
from services.task_execution.contracts import PlanStep,WorkPlan
from services.task_execution.reducer import revise_plan
from services.tools.catalog import build_capability_catalog
from tests.test_task_execution_bootstrap import bootstrap,proposal
from tests.test_task_execution_methods import document
from tests.test_task_execution_model_bridge import setup_case,execute,call

pytestmark=pytest.mark.skipif(not os.getenv('TASK_ORIGINAL_SKILLS_EXPORT'),reason='requires private original published Skill inputs')


class OriginalSource:
    def __init__(self):
        self.values={};self.candidates=[];self.assets={}
        for row in json.loads(Path(os.environ['TASK_ORIGINAL_SKILLS_EXPORT']).read_text()):
            p,r=row['package'],row['revision']
            package=SkillPackage.model_validate({k:v for k,v in p.items() if k in SkillPackage.model_fields})
            validated=SkillStorage.validate_bytes(package,PublishRevision(**{k:r[k] for k in PublishRevision.model_fields}),
                base64.b64decode(row['raw_b64']))
            assert (str(package.id),package.skill_key,r['revision'],validated.body_sha256) in methods.PROFILES
            candidate=SkillCandidate(package_id=package.id,skill_key=package.skill_key,revision=r['revision'],
                package_org_id=package.org_id,package_user_id=package.owner_user_id,assignment_org_id=package.org_id,
                scope_kind=package.scope_kind,priority=0,description=r['summary'],catalog_metadata=validated.catalog_metadata)
            self.values[candidate.skill_key]=validated;self.candidates.append(candidate);self.assets[candidate.skill_key]=row['assets']
        assert set(self.values)=={'reference-image-prompts','ecommerce-image-prompts'}

    async def discover(self): return self.candidates
    async def session_bindings(self): return []
    async def load(self,candidate,*,restoring=False): return self.values[candidate.skill_key]
    async def load_assets(self,candidate,validated,ids):
        declared={a.id:a for a in validated.resources.assets}
        return {key:verify_asset(declared[key],self.assets[candidate.skill_key][key].encode()) for key in ids}


def original_runtime(source,*,task_state=None,turn_id=None):
    from core.config import get_settings
    settings=get_settings()
    return SkillRuntime(turn_id=turn_id or str(uuid4()),source=source,
        platform_tool_names={'generate_image','file_search'},authorized_tool_names={'generate_image','file_search'},
        cancellation_event=asyncio.Event(),maximum_body=settings.skill_max_body_bytes,
        maximum_rendered=settings.skill_max_rendered_bytes,maximum_turn_rendered=settings.skill_max_turn_rendered_bytes,
        task_execution_state=task_state)


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['reference-image-prompts','ecommerce-image-prompts'])
async def test_exact_original_skill_gates_full_plan_and_restores_complete_context(key):
    source=OriginalSource();skill=source.values[key]
    selection=SkillSelection(skill_id=key,revision=skill.revision)
    skills=original_runtime(source)
    await skills.initialize(selection=selection)
    assert (await skills.activate_manual(selection))['ok']
    assert skills.active[key].body_sha256==skill.body_sha256
    assert len(skills.active[key].rendered.encode())>=len(skill.body.encode())
    initial,*_=bootstrap()
    state=methods.attach_methods(initial.initialize({**proposal(),'action':'plan','applicable_method_ids':[key]},model_rounds_used=1),skills)
    required=state.method_bindings[0].requirement_ids
    state=revise_plan(state,WorkPlan(expected_revision=state.revision,steps=(
        PlanStep(id='planning',title='完整方法规划',requirement_ids=('user.prompts',*required)),
        PlanStep(id='images',title='五张图',requirement_ids=('user.images',),depends_on=('planning',),capability='platform.generate_image'))))
    assert 'generate_image' in methods.blocked_tool_names(state,build_capability_catalog())
    runtime,prepared,handler,sink=setup_case(initial=state);runtime.skill_runtime=skills
    prepared.messages.insert(0,{'role':'system','content':'原记忆和会话摘要保持不变'})
    blocks=[]
    await execute(runtime,prepared,handler,sink,[call('save_task_text',{
        'expected_revision':state.revision,'step_id':'planning','text':document()},'save')],blocks)
    artifact=runtime.task_execution_state.artifacts[0]
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[artifact.id]},'complete')],blocks)
    skills=runtime.skill_runtime
    assert skills.manual_selection==selection and prepared.messages[0]['content']=='原记忆和会话摘要保持不变'
    assert ('generate_image' in skills.effective_allowed_tool_names)
    assert (key in skills.resident)==(key=='reference-image-prompts')
    checkpoint=skills.checkpoint()
    restored=original_runtime(source,task_state=runtime.task_execution_state,turn_id=skills.turn_id)
    await restored.initialize(checkpoint)
    assert restored.checkpoint()==checkpoint
    assert restored.manual_selection==selection
    assert not methods.blocked_tool_names(runtime.task_execution_state,build_capability_catalog())
    assert source.values[key].body_sha256==skill.body_sha256
    handler._execute_tool_calls.assert_not_called()
