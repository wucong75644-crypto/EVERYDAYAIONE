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
