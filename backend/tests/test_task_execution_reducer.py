"""Logical-task invariants, without provider calls or mocked dispatch success."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.task_execution.contracts import (Artifact, CapabilityGate, ExecutionIdentity, PlanStep,
    Requirement, TaskError, TaskState, WorkPlan)
from services.task_execution.reducer import (complete_step, consume_model_round, digest, finish,
    missing_requirements, ready_steps, record_operation, register_artifacts, reserve_operation, revise_plan)


def state(**overrides):
    source = uuid4()
    actor = uuid4()
    return TaskState(root_task_id=uuid4(), conversation_id=uuid4(), actor_user_id=actor,
        execution_identity=ExecutionIdentity(workspace_owner_id=str(actor), context_scope='user', personal_context_allowed=True),
        source_message_ids=(source,), original_goal='研究后生成报告', requirements=(
            Requirement(id='report', source_message_id=source, description='交付报告文件', artifact_kind='file'),
        ), **overrides)


def plan(s, steps=None):
    return revise_plan(s, WorkPlan(expected_revision=s.revision, steps=steps or (
        PlanStep(id='research', title='研究来源', capability='web_search'),
        PlanStep(id='report', title='写报告', depends_on=('research',), requirement_ids=('report',)),
    )))


def artifact(s, step, kind='text', key=None, reference=None, validations=('artifact.exists',)):
    return Artifact(id=key or step, kind=kind, step_id=step, reference=reference or f'workspace:{step}',
                    sha256=digest(step), source_task_id=s.root_task_id, source_call_id='call:1',
                    validations=validations)


def test_dependency_drives_readiness_and_real_artifacts_drive_completion():
    initial = state()
    s = plan(initial)
    assert [v.id for v in ready_steps(s)] == ['research']
    with pytest.raises(TaskError, match='NOT_READY'):
        complete_step(s, 'report', ['fiction'])
    with pytest.raises(TaskError, match='EVIDENCE_MISSING'):
        complete_step(s, 'research', ['fiction'])
    s = register_artifacts(s, [artifact(s, 'research')])
    s = complete_step(s, 'research', ['research'])
    assert [v.id for v in ready_steps(s)] == ['report']
    s = register_artifacts(s, [artifact(s, 'report', 'file')])
    s = complete_step(s, 'report', ['report'])
    assert finish(s).status == 'completed'
    assert initial.steps == ()  # candidate operations do not partially mutate live state


@pytest.mark.parametrize('steps', [
    [PlanStep(id='a', title='a', depends_on=('a',))],
    [PlanStep(id='a', title='a', depends_on=('b',)), PlanStep(id='b', title='b', depends_on=('a',))],
    [PlanStep(id='a', title='a', depends_on=('missing',))],
    [PlanStep(id='a', title='a'), PlanStep(id='a', title='a')],
])
def test_rejects_invalid_dependency_graph(steps):
    with pytest.raises(ValidationError):
        WorkPlan(expected_revision=0, steps=steps)


def test_plan_cannot_remove_or_downgrade_user_requirement():
    s = plan(state())
    with pytest.raises(TaskError, match='REQUIREMENTS_NOT_COVERED'):
        revise_plan(s, WorkPlan(expected_revision=s.revision, steps=[PlanStep(id='a', title='结束')]))
    with pytest.raises(ValidationError):
        WorkPlan.model_validate({'expected_revision': s.revision, 'steps': [], 'requirements': []})
    with pytest.raises(ValidationError):
        PlanStep.model_validate({'id': 'report', 'title': '报告', 'required': False})


def test_changed_plan_cannot_rebind_completed_step_or_its_evidence():
    s = plan(state())
    a = artifact(s, 'research')
    s = register_artifacts(s, [a])
    assert register_artifacts(s, [a]) is s
    with pytest.raises(TaskError, match='ID_REUSED'):
        register_artifacts(s, [artifact(s, 'report', key='research')])
    with pytest.raises(TaskError, match='STEP_IMMUTABLE'):
        revise_plan(s, WorkPlan(expected_revision=s.revision, steps=[
            PlanStep(id='research', title='研究', requirement_ids=('report',)),
        ]))


def test_receipt_success_does_not_substitute_for_requested_file():
    s = plan(state(), (PlanStep(id='report', title='报告', requirement_ids=('report',)),))
    s = register_artifacts(s, [artifact(s, 'report', 'receipt')])
    with pytest.raises(TaskError, match='STEP_DELIVERY_INCOMPLETE'):
        complete_step(s, 'report', ['report'])
    assert missing_requirements(s) == ('report',)
    with pytest.raises(TaskError, match='DELIVERY_INCOMPLETE'):
        finish(s)


def test_duplicate_resource_does_not_satisfy_required_count_and_domain_check_is_required():
    s = state()
    requirement = s.requirements[0].model_copy(update={'minimum': 2, 'validator': 'file.readable'})
    s = TaskState.model_validate({**s.model_dump(), 'requirements': [requirement]})
    s = plan(s, (PlanStep(id='report', title='报告', requirement_ids=('report',)),))
    a = artifact(s, 'report', 'file', reference='workspace:one', validations=('file.readable',))
    b = a.model_copy(update={'id': 'duplicate'})
    s = register_artifacts(s, [a, b])
    with pytest.raises(TaskError, match='STEP_DELIVERY_INCOMPLETE'):
        complete_step(s, 'report', ['report', 'duplicate'])
    assert missing_requirements(s) == ('report',)


def test_cross_generation_call_id_change_cannot_reserve_effect_twice():
    s = plan(state(), (PlanStep(id='report', title='报告', capability='code_execute', requirement_ids=('report',)),))
    first_owner, next_owner = uuid4(), uuid4()
    s, op, decision = reserve_operation(s, expected_revision=s.revision, step_id='report', capability='code_execute', effect_key='report-file',
        effect_version=1, arguments={'path': 'report.pdf'}, owner_task_id=first_owner, invocation_id='call:1')
    assert decision == 'reserve'
    same, repeated, decision = reserve_operation(s, expected_revision=s.revision, step_id='report', capability='code_execute', effect_key='report-file',
        effect_version=1, arguments={'path': 'report.pdf'}, owner_task_id=next_owner, invocation_id='new-call')
    assert same is s and repeated == op and decision == 'reconcile'
    with pytest.raises(TaskError, match='OWNER_MISMATCH'):
        record_operation(s, op.id, owner_task_id=next_owner, status='completed', receipt_ref='file:one')
    s = record_operation(s, op.id, owner_task_id=first_owner, status='accepted', receipt_ref='file:one')
    same, repeated, decision = reserve_operation(s, expected_revision=s.revision, step_id='report', capability='code_execute', effect_key='report-file',
        effect_version=1, arguments={'path': 'report.pdf'}, owner_task_id=next_owner, invocation_id='third-call')
    assert same is s and decision == 'replay' and repeated.receipt_ref == 'file:one'


def test_changed_arguments_do_not_make_uncertain_operation_safe_to_repeat():
    s = plan(state())
    owner = uuid4()
    s, op, _ = reserve_operation(s, expected_revision=s.revision, step_id='research', capability='web_search', effect_key='image-1', effect_version=1,
        arguments={'prompt': 'original'}, owner_task_id=owner, invocation_id='one')
    s = record_operation(s, op.id, owner_task_id=owner, status='uncertain')
    with pytest.raises(TaskError, match='ARGUMENTS_CHANGED'):
        reserve_operation(s, expected_revision=s.revision, step_id='research', capability='web_search', effect_key='image-1', effect_version=1,
            arguments={'prompt': 'changed'}, owner_task_id=uuid4(), invocation_id='two')
    with pytest.raises(TaskError, match='TRANSITION_DENIED'):
        record_operation(s, op.id, owner_task_id=owner, status='failed_before_dispatch')


def test_round_and_correction_budgets_survive_serialization_and_continuation():
    s = state(model_round_limit=2, correction_limit=1)
    s = consume_model_round(s, correction=True)
    restored = TaskState.model_validate_json(s.model_dump_json())
    with pytest.raises(TaskError, match='BUDGET_EXHAUSTED'):
        consume_model_round(restored, correction=True)
    restored = consume_model_round(restored)
    with pytest.raises(TaskError, match='BUDGET_EXHAUSTED'):
        consume_model_round(restored)


def test_parallel_ready_steps_and_stale_revision():
    s = state()
    steps = (PlanStep(id='a', title='source A'), PlanStep(id='b', title='source B'),
             PlanStep(id='report', title='报告', depends_on=('a', 'b'), requirement_ids=('report',)))
    s = plan(s, steps)
    assert {step.id for step in ready_steps(s)} == {'a', 'b'}
    with pytest.raises(TaskError, match='REVISION_STALE'):
        revise_plan(s, WorkPlan(expected_revision=0, steps=steps))


def test_terminal_tasks_cannot_plan_or_dispatch_more_effects():
    s = state().model_copy(update={'status': 'cancelled'})
    with pytest.raises(TaskError, match='NOT_ACTIVE'):
        plan(s)
    with pytest.raises(TaskError, match='NOT_ACTIVE'):
        consume_model_round(s)


def test_parallel_item_contributions_accumulate_without_requiring_five_items_per_step():
    s = state()
    requirement = s.requirements[0].model_copy(update={'minimum': 5, 'artifact_kind': 'image'})
    s = TaskState.model_validate({**s.model_dump(), 'requirements': [requirement]})
    s = plan(s, tuple(PlanStep(id=f'image-{n}', title='图片', contributes_to=('report',)) for n in range(5)))
    for n in range(5):
        step = f'image-{n}'
        s = register_artifacts(s, [artifact(s, step, 'image')])
        s = complete_step(s, step, [step])
    assert missing_requirements(s) == ()
    assert finish(s).status == 'completed'


def test_host_gate_cannot_be_removed_by_model_dependency_or_plan_edits():
    s = state()
    source = s.source_message_ids[0]
    s = TaskState.model_validate({**s.model_dump(), 'requirements': [
        Requirement(id='prompts', source_message_id=source, description='完整提示词',
                    artifact_kind='text', minimum=5, validator='prompts.complete'),
        Requirement(id='images', source_message_id=source, description='图片', artifact_kind='image', minimum=5),
    ], 'capability_gates': [CapabilityGate(capability='generate_image', requirement_ids=('prompts',))]})
    s = plan(s, (PlanStep(id='planning', title='规划', requirement_ids=('prompts',)),
                 PlanStep(id='generate', title='生图', capability='generate_image', requirement_ids=('images',))))
    assert [step.id for step in ready_steps(s)] == ['planning']
    s = register_artifacts(s, [artifact(s, 'planning', 'receipt', key='receipt')])
    with pytest.raises(TaskError, match='DELIVERY_INCOMPLETE'):
        complete_step(s, 'planning', ['receipt'])
    with pytest.raises(TaskError, match='NOT_READY'):
        reserve_operation(s, expected_revision=s.revision, step_id='generate', capability='generate_image',
                          effect_key='first-image', effect_version=1, arguments={},
                          owner_task_id=s.root_task_id, invocation_id='one')
    items = [artifact(s, 'planning', key=f'prompt-{n}', reference=f'prompt:{n}',
                      validations=('artifact.exists', 'prompts.complete')) for n in range(5)]
    s = register_artifacts(s, items)
    s = complete_step(s, 'planning', [a.id for a in items])
    assert [step.id for step in ready_steps(s)] == ['generate']


def test_effect_version_cannot_bypass_uncertain_or_trusted_requirement_revision():
    s = plan(state())
    owner = uuid4()
    s, op, _ = reserve_operation(s, expected_revision=s.revision, step_id='research', capability='web_search',
        effect_key='same-effect', effect_version=1, arguments={'x': 1}, owner_task_id=owner, invocation_id='one')
    s = record_operation(s, op.id, owner_task_id=owner, status='uncertain')
    with pytest.raises(TaskError, match='EFFECT_UNRESOLVED'):
        reserve_operation(s, expected_revision=s.revision, step_id='research', capability='web_search',
            effect_key='same-effect', effect_version=2, arguments={'x': 2}, owner_task_id=uuid4(), invocation_id='two')
    s = record_operation(s, op.id, owner_task_id=owner, status='completed', receipt_ref='result:one')
    with pytest.raises(TaskError, match='REVISION_UNAUTHORIZED'):
        reserve_operation(s, expected_revision=s.revision, step_id='research', capability='web_search',
            effect_key='same-effect', effect_version=2, arguments={'x': 2}, owner_task_id=uuid4(), invocation_id='two')


def test_completed_effect_freezes_its_step_not_unexecuted_future_plan():
    s = plan(state())
    owner = uuid4()
    s, op, _ = reserve_operation(s, expected_revision=s.revision, step_id='research', capability='web_search',
        effect_key='lookup', effect_version=1, arguments={}, owner_task_id=owner, invocation_id='one')
    s = record_operation(s, op.id, owner_task_id=owner, status='completed', receipt_ref='source:one')
    s = register_artifacts(s, [artifact(s, 'research')])
    s = complete_step(s, 'research', ['research'])
    changed = s.steps[1].model_copy(update={'title': '根据查询结果写报告'})
    result = revise_plan(s, WorkPlan(expected_revision=s.revision, steps=(s.steps[0], changed)))
    assert result.steps[1].title == changed.title
    assert result.operations == s.operations


def test_checkpoint_cannot_claim_completion_without_delivery_evidence():
    s = plan(state())
    with pytest.raises(ValidationError, match='DELIVERY_INCOMPLETE'):
        TaskState.model_validate({**s.model_dump(), 'status': 'completed'})
    with pytest.raises(ValidationError, match='RESULT_INCONSISTENT'):
        TaskState.model_validate({**s.model_dump(), 'results': [
            {'step_id': 'research', 'artifact_ids': ['nonexistent']}]})


def test_one_text_artifact_can_cover_separately_declared_content_obligations():
    import hashlib
    base=state();source=base.source_message_ids[0]
    initial=base.model_copy(update={'original_goal':'一份文档：商品分析和五条提示词','requirements':(
        Requirement(id='analysis',source_message_id=source,description='商品分析',artifact_kind='text'),
        Requirement(id='prompts',source_message_id=source,description='五条提示词',artifact_kind='text',item_count=5))})
    current=revise_plan(initial,WorkPlan(expected_revision=initial.revision,steps=(
        PlanStep(id='document',title='完整文档',requirement_ids=('analysis','prompts')),)))
    text='商品分析：用户提供的风琴包。\n'+'\n'.join(f'提示词{i}：第{i}种构图。' for i in range(1,6))
    evidence=Artifact(id='one-document',kind='text',reference='task-text:one-document',sha256=hashlib.sha256(text.encode()).hexdigest(),
        source_task_id=current.root_task_id,source_call_id='save-document',step_id='document',inline_text=text)
    current=register_artifacts(current,[evidence]);current=complete_step(current,'document',[evidence.id])
    assert finish(current).status=='completed' and not missing_requirements(current)
    assert len(current.artifacts)==1 and current.requirements[1].item_count==5
