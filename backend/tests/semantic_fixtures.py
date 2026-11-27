"""Frozen upstream decisions for consumer tests; never classify test wording."""
from dataclasses import is_dataclass, replace
from services.agent.image.ecommerce_planner.workflow import ImageRequestContext


def semantic_request(request, intent, **context):
    frame = ImageRequestContext.model_validate(context)
    params = {**(getattr(request, 'params', None) or {}), '_ecom_workflow': {
        'intent': intent, 'request_context': frame.model_dump(mode='json', exclude_none=True)}}
    if is_dataclass(request):
        return replace(request, params=params)
    from copy import copy
    result = copy(request)
    result.params = params
    return result


def observed_revision(runtime, request, row, indices=None):
    """Consumer fixture: a scoped read of this immutable version already succeeded.

    End-to-end read-before-write behavior is tested through the actual READ
    tool separately; consumers here exercise revision/approval invariants.
    """
    from services.task_execution.content_objects import _load_record
    from services.task_execution.content_revision import record_read
    for index in indices or [i['item_index'] for i in row['items']]:
        artifact, record, _ = _load_record(runtime.task_execution_state,
            {'artifact_id': row['artifact_id'], 'item_index': index})
        record_read(runtime, request, artifact, record, index)
