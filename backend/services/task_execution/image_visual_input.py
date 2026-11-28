"""Host-owned identities of images actually attached to a model request.

Textual metadata, file names and model-written claims never create a receipt.
The pending view affects tool offers only; admission uses committed receipts.
"""
import copy

SOURCES = '_visual_input_sources'
PART_SOURCE = '_visual_reference'
HOST_CONTEXT = '_host_context_kind'


def image_url(part):
    if part.get('type') not in {'image_url', 'input_image'}:
        return None
    value = part.get('image_url')
    return value.get('url') if isinstance(value, dict) else value


def prepare(runtime, messages):
    from .image_history import scope, canonical_reference
    snapshot = (getattr(runtime, 'checkpoint_base', {}) or {}).get('_request_context_v1') or {}
    if snapshot.get('image_reference_version') != 1:
        return None
    known = [image['reference'] for row in snapshot.get('requests', ()) for image in row.get('images', ())]
    known += [{'message_id': image['source_message_id'], 'content_index': image['content_index']}
              for image in snapshot.get('image_results', ())]
    from .image_history import recovered
    known += [image['reference'] for image in recovered(runtime)]
    known += [{'resource_ref': attachment.resource_ref}
              for change in getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
              if change.delivered and str(change.owner_task_id) == str(runtime.task_id)
              for attachment in change.attachments if attachment.kind == 'image']
    known = [canonical_reference(runtime, ref) for ref in known]
    references = []
    for message in messages:
        parts = message.get('content')
        if not isinstance(parts, list):
            continue
        urls = {image_url(part) for part in parts if isinstance(part, dict)} - {None}
        sources = list(message.get(SOURCES, ()))
        sources += [{'url': image_url(part), 'reference': part[PART_SOURCE]}
                    for part in parts if isinstance(part, dict) and part.get(PART_SOURCE)]
        for source in sources:
            ref = canonical_reference(runtime, source['reference'])
            if source.get('url') in urls and ref in known and ref not in references:
                references.append(ref)
    token = {'scope': scope(runtime), 'owner_task_id': str(runtime.task_id), 'references': references}
    runtime.pending_image_inputs = token
    return copy.deepcopy(token)


def includes(runtime, reference):
    from .image_history import scope, canonical_reference
    token = getattr(runtime, 'pending_image_inputs', None)
    return bool(token and token['owner_task_id'] == str(runtime.task_id)
                and token['scope'] == scope(runtime)
                and canonical_reference(runtime, reference) in token['references'])


def commit(runtime, token):
    from .image_history import scope, mark_viewed
    if (token and token['owner_task_id'] == str(runtime.task_id) and token['scope'] == scope(runtime)):
        for reference in token['references']:
            mark_viewed(runtime, reference)


def clear(runtime):
    if runtime is not None:
        runtime.pending_image_inputs = None
