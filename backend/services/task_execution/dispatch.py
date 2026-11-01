"""Project DAG readiness onto native tools without granting tool access.

This is a capability barrier, not an input/operation binding. Unplanned support
tools retain their existing policy path; real result/effect adapters must still
bind each business call to its inputs, output slot and durable invocation.
"""
from .contracts import TaskError
from .reducer import ready_steps


def validate_plan_capabilities(proposal, registry, *, state=None):
    known = {spec.capability for spec in registry.specs() if spec.capability}
    if any(step.capability is not None and step.capability not in known for step in proposal.steps):
        raise TaskError('TASK_PLAN_CAPABILITY_UNKNOWN')
    if state is not None and state.image_binding_version>=3:
        from .native_writes import erp_write_dispatch_enabled
        old_erp_steps={o.step_id for o in state.operations if o.tool_name=='erp_execute'}
        if not erp_write_dispatch_enabled() and any(s.capability=='platform.erp_execute' and s.id not in old_erp_steps for s in proposal.steps):
            raise TaskError('TASK_ERP_WRITE_ADAPTER_DEFERRED')


def annotate_task_tools(tools, registry):
    """Describe only already disclosed tools; never expand the tool inventory."""
    result = []
    for tool in tools:
        function = tool['function']
        spec = registry.get(function['name'])
        if spec is not None and spec.capability:
            function = {**function, 'description': function.get('description', '')
                + f'\n任务计划 capability 使用目录标识：{spec.capability}。此标识不授予执行权限。'}
            tool = {**tool, 'function': function}
        result.append(tool)
    return result


def plan_dispatch_blockers(state, registry):
    if state is None:
        return {}
    planned = {step.capability for step in state.steps if step.capability}
    ready = {step.capability for step in ready_steps(state) if step.capability}
    completed = {result.step_id for result in state.results}
    blockers = {}
    for spec in registry.specs():
        if state.image_binding_version>=3 and spec.name=='erp_execute':
            from .native_writes import erp_write_dispatch_enabled
            if not erp_write_dispatch_enabled() and not any(o.tool_name=='erp_execute' for o in state.operations):
                blockers[spec.name]='TASK_ERP_WRITE_ADAPTER_DEFERRED'
                continue
        if spec.capability not in planned:
            if state.image_binding_version>=3 and spec.policy_rules.operation in {'business_write','generation'}:
                blockers[spec.name]='TASK_CAPABILITY_NOT_PLANNED'
            continue
        if state.status != 'active':
            blockers[spec.name] = 'TASK_NOT_ACTIVE'
        elif spec.capability not in ready:
            steps = [step for step in state.steps if step.capability == spec.capability]
            blockers[spec.name] = ('TASK_STEP_COMPLETED' if all(step.id in completed for step in steps)
                                   else 'TASK_DEPENDENCY_INCOMPLETE')
    return blockers


def blocker_message(code):
    if code == 'TASK_ERP_WRITE_ADAPTER_DEFERRED':
        return '本次新编排尚未开放ERP写操作。保留已有成果并继续其他可执行步骤；ERP查询仍可使用原ERP Agent。'
    if code == 'TASK_STEP_COMPLETED':
        return '该能力对应的计划步骤已完成；请复用已有成果，继续尚未完成的步骤。'
    if code == 'TASK_NOT_ACTIVE':
        return '当前逻辑任务不在可执行状态，请依据已保存状态等待或收尾。'
    return '该工具对应的计划步骤尚有未完成依赖。请先交付前置成果并完成相应步骤，再继续调用。'
