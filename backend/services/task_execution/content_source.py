"""Scoped, read-only draft sources. Resolving a source never adopts its task."""
import copy
import json
from dataclasses import dataclass

from .contracts import TaskError, TaskState
from . import content_objects as co


class ContentSourceError(TaskError):
    def repair(self):
        code = str(self)
        action, message = {
            'CONTENT_SOURCE_SCOPE_DENIED': ('request_user_input', '指定稿件不属于本次可访问范围，停止使用该来源。'),
            'CONTENT_SOURCE_LOAD_FAILED': ('reload_source', '后台暂时无法恢复指定稿件来源，保留原编号与修改内容，重新读取同一来源。'),
            'CONTENT_BASE_VERSION_CHANGED': ('verify_version_then_read', '指定稿件版本已变化，先核对当前目录和用户指定版本，再读取；不自动换成最新版。'),
        }.get(code, ('verify_source_then_read', '指定稿件来源尚无法核实，核对当前目录并读取所选原稿。'))
        return {'ok': False, 'accepted': False, 'code': code, 'submission_state': 'not_dispatched',
            'recovery': {'action': action, **({'tool': 'read_conversation_text'} if action != 'request_user_input' else {})},
            'message': message + '本次未保存、未接管、未派发；不要修改有效编号、重建另一组或自动改选其他任务。'}



@dataclass(frozen=True)
class Source:
    state: TaskState
    handoff: dict | None = None


def _scope(runtime):
    from .reducer import digest
    from .content_revision import input_revision
    request = getattr(runtime, '_content_source_request', None)
    return digest([str(runtime.task_id), input_revision(runtime, request),
                   runtime.checkpoint_base.get('_request_resolution_v1')])


def _cache(runtime):
    cached = getattr(runtime, '_resolved_content_sources', None)
    return cached['items'] if cached and cached['scope'] == _scope(runtime) else {}


def state_for(runtime, reference):
    """Only the specified artifact can identify a source; no recent-task fallback."""
    reference = co.ContentReference.model_validate(reference)
    current = getattr(runtime, 'task_execution_state', None)
    if current is not None:
        return current
    source = _cache(runtime).get(reference.artifact_id)
    if source is not None:
        return source.state
    candidate = getattr(runtime, 'task_followup_candidate', None)
    if candidate and candidate.get('state'):
        state = TaskState.model_validate(candidate['state'])
        if any(a.id == reference.artifact_id for a in co.objects(state)):
            return state
    return None


def _check_identity(state, context):
    if context is None:
        return
    if (str(state.conversation_id), str(state.org_id), str(state.actor_user_id)) != (
            str(context.conversation_id), str(context.org_id), str(context.actor_user_id)):
        raise ContentSourceError('CONTENT_SOURCE_SCOPE_DENIED')
    identity = state.execution_identity
    if (identity.workspace_owner_id != str(context.workspace_owner_id)
            or identity.context_scope != context.context_scope
            or identity.personal_context_allowed != context.personal_context_allowed):
        raise ContentSourceError('CONTENT_SOURCE_SCOPE_DENIED')


async def resolve(runtime, reference, *, context=None, refresh=False):
    reference = co.ContentReference.model_validate(reference)
    current = getattr(runtime, 'task_execution_state', None)
    source = None
    state = current
    if current is None:
        cached = _cache(runtime).get(reference.artifact_id)
        if cached is not None and not refresh:
            state, source = cached.state, cached.handoff
        else:
            candidate = getattr(runtime, 'task_followup_candidate', None)
            candidate_state = TaskState.model_validate(candidate['state']) if candidate and candidate.get('state') else None
            roots = {str(g['root_task_id']) for g in getattr(runtime, 'task_content_groups', ())
                     if g['artifact_id'] == reference.artifact_id}
            if candidate_state is not None and any(a.id == reference.artifact_id for a in co.objects(candidate_state)):
                roots.add(str(candidate_state.root_task_id))
                state, source = candidate_state, candidate
            if len(roots) != 1:
                raise ContentSourceError('CONTENT_SOURCE_UNAVAILABLE')
            root = roots.pop()
            reader = getattr(runtime, 'read_content_root', None)
            if reader is not None:
                try:
                    source = await reader(root)
                    if not source:
                        raise ContentSourceError('CONTENT_SOURCE_UNAVAILABLE')
                    state = TaskState.model_validate(source['state'])
                except ContentSourceError:
                    raise
                except PermissionError as error:
                    raise ContentSourceError('CONTENT_SOURCE_SCOPE_DENIED') from error
                except Exception as error:
                    raise ContentSourceError('CONTENT_SOURCE_LOAD_FAILED') from error
            if state is None:
                raise ContentSourceError('CONTENT_SOURCE_UNAVAILABLE')
            if str(state.root_task_id) != root:
                raise ContentSourceError('CONTENT_SOURCE_SCOPE_DENIED')
    _check_identity(state, context)
    artifact, record, _ = co._load_record(state, reference)
    selected = runtime.checkpoint_base.get('_request_resolution_v1') or {}
    target = selected.get('target') or {}
    if selected.get('status') == 'resolved' and target.get('artifact_id') == artifact.id:
        if target.get('root_task_id') and str(state.root_task_id) != str(target['root_task_id']):
            raise ContentSourceError('CONTENT_SOURCE_SCOPE_DENIED')
        if target.get('revision') is not None and target['revision'] != record.revision:
            raise ContentSourceError('CONTENT_BASE_VERSION_CHANGED')
    if not co.is_current(state, artifact.id):
        raise ContentSourceError('CONTENT_BASE_VERSION_CHANGED')
    result = Source(state, copy.deepcopy(source))
    items = dict(_cache(runtime))
    items[reference.artifact_id] = result
    runtime._resolved_content_sources = {'scope': _scope(runtime), 'items': items}
    if target.get('artifact_id') == artifact.id:
        runtime._content_source_error = None
    return result


async def prepare_selected(runtime, request, context=None):
    from .request_resolution import binding
    runtime._content_source_request = request
    runtime._resolved_content_sources = None
    runtime._content_source_error = None
    selected = binding(runtime) or {}
    target = selected.get('target') or {}
    if selected.get('status') == 'resolved' and target.get('kind') == 'content' and target.get('item_indices'):
        try:
            from .content_revision import saved_reference
            reference = saved_reference(runtime, request, target) or {
                'artifact_id': target['artifact_id'], 'item_index': target['item_indices'][0]}
            await resolve(runtime, reference, context=context, refresh=True)
        except ValueError as error:
            runtime._content_source_error = error if isinstance(error, ContentSourceError) else ContentSourceError(str(error))


async def prepare_calls(runtime, request, calls, context=None):
    runtime._content_source_request = request
    runtime._resolved_content_sources = None
    errors = {}
    for call in calls:
        if call['name'] not in {co.SAVE, 'generate_image'}:
            continue
        try:
            value = json.loads(call['arguments'])
            if not isinstance(value, dict):
                continue
            reference = value.get('base') if call['name'] == co.SAVE else value.get('content_source')
            if not isinstance(reference, dict):
                continue
            reference = {k: reference[k] for k in ('artifact_id', 'item_index', 'sha256') if k in reference}
            # Unknown selectors remain owned by the original tool's argument
            # and source validation, including ordinary inline-image repair.
            identity = reference.get('artifact_id')
            if not any(g['artifact_id'] == identity for g in getattr(runtime, 'task_content_groups', ())):
                available = state_for(runtime, reference)
                if available is None or not any(a.id == identity for a in co.objects(available)):
                    continue
            await resolve(runtime, reference, context=context, refresh=True)
        except ContentSourceError as error:
            errors[call['id']] = error
        except (ValueError, TypeError, KeyError):
            continue  # Original shape and strict-JSON validation owns malformed calls.
    return errors


def call_state(runtime, call):
    try:
        value = json.loads(call['arguments'])
        if not isinstance(value, dict):
            return getattr(runtime, 'task_execution_state', None)
        ref = value.get('base') if call['name'] == co.SAVE else value.get('content_source')
        if isinstance(ref, dict):
            return state_for(runtime, {k: ref[k] for k in ('artifact_id', 'item_index', 'sha256') if k in ref})
    except (ValueError, TypeError, KeyError):
        pass
    return getattr(runtime, 'task_execution_state', None)
