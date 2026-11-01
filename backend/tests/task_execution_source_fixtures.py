"""Trusted legacy-import state fixture for lifecycle integration tests."""

def existing_source_state(state):
    from services.task_execution.contracts import Requirement,TaskState
    from services.task_execution.image_sources import SOURCE_REQUIREMENT,SOURCE_VALIDATOR,has_source_handoff
    original=state.steps[0].requirement_ids
    source=Requirement(id=SOURCE_REQUIREMENT,source_message_id=state.source_message_ids[0],
        description='原方案',artifact_kind='text',validator=SOURCE_VALIDATOR)
    state=TaskState.model_validate({**state.model_dump(),'intent_action':'execute_ready','method_bindings':(),
        'requirements':tuple(r for r in state.requirements if r.id not in original)+(source,),
        'steps':(state.steps[0].model_copy(update={'requirement_ids':(SOURCE_REQUIREMENT,)}),*state.steps[1:]),
        'capability_gates':tuple(g.model_copy(update={'requirement_ids':tuple(
        SOURCE_REQUIREMENT if key in original else key for key in g.requirement_ids)}) for g in state.capability_gates),
        'artifacts':tuple(a.model_copy(update={'reference':'legacy-message:'+str(state.source_message_ids[0]),
        'validations':('artifact.exists',SOURCE_VALIDATOR)}) for a in state.artifacts)})
    assert has_source_handoff(state) and not state.method_bindings

    return state
