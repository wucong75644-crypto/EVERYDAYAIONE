"""Bounded, cursor-based discovery inside an authenticated input's frozen history."""
from __future__ import annotations

import copy
import hashlib
import json

from pydantic import Field
from typing import Literal

from .contracts import Fact

TOOL = 'list_conversation_images'
KEY = '_historical_image_catalog_v1'


class ListImages(Fact):
    order: Literal['oldest_first', 'newest_first'] = 'oldest_first'
    source: Literal['generated', 'uploaded', 'all'] = 'all'
    limit: int = Field(default=10, strict=True, ge=1, le=20)
    cursor: str | None = Field(default=None, min_length=1, max_length=100)


GUIDANCE = (
    '最近图片目录不是全部历史。找不到用户指定旧图时，用本工具分页查找当前会话的历史图片；'
    'order=oldest_first查最早，newest_first查最近；source=generated只查成图，uploaded只查上传，all查全部。'
    '例如最早灯塔成图：{"order":"oldest_first","source":"generated","limit":10}。'
    'has_more=true时原样复制next_cursor继续，例如{"order":"oldest_first","source":"generated","limit":10,"cursor":"复制next_cursor"}。'
    '也可仅传{"cursor":"复制next_cursor","limit":10}，后台沿用游标绑定的排序和来源；显式填写order/source时必须与原查询一致。空页仍可能有下一页。'
    '回执request_excerpt仅是原消息摘要，不能作为图片视觉事实。每个候选的next_step是当前下一步；'
    'requires_view_before_selection=true时先核图，false时可按selection_arguments选择。核图参数为read_conversation_text的'
    '{"view_image":{"image_ref":"复制候选image_ref"}}核图，再用resolve_request_target明确本次目标：'
    '{"intent":"explain","request_context":{"focus":"selected_target","image_action":"display_existing",'
    '"target":{"kind":"image","basis":"explicit_reference","image_ref":"复制核实后的image_ref"}}}。'
    '用户只要看旧图时保持intent=explain、image_action=display_existing；使用show_image或已选目标自动交付，'
    '例如read_conversation_text参数{"show_image":{"image_ref":"复制核实后的image_ref"}}，不是重新生成。'
    '编辑先把intent纠正为edit、image_action=null并保持同一target，再用generate_image参数'
    '{"image_ref":"复制核实后的image_ref","edit_instruction":"只把桌布换成浅蓝色，封面图案和文字别动"}。'
    '示例中的复制文字是占位说明，必须替换为真实回执。多个候选无法核实时询问用户，不默认第一张或最近图。'
    '查询及看图不代表已经给用户展示，不新增执行授权。'
)
SCHEMA = {'type': 'function', 'function': {'name': TOOL, 'description': GUIDANCE,
    'parameters': ListImages.model_json_schema()}}


def scope(runtime):
    snapshot = (getattr(runtime, 'checkpoint_base', {}) or {}).get('_request_context_v1')
    if not snapshot or snapshot.get('image_reference_version') != 1:
        raise ValueError('CHAT_IMAGE_HISTORY_UNAVAILABLE')
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


def recovered(runtime):
    journal = (getattr(runtime, 'checkpoint_base', {}) or {}).get(KEY) or {}
    try:
        return journal.get('images', []) if journal.get('scope') == scope(runtime) else []
    except ValueError:
        return []


def canonical_reference(runtime, reference):
    """Only frozen server-verified redisplay aliases can identify the original."""
    snapshot = (getattr(runtime, 'checkpoint_base', {}) or {}).get('_request_context_v1') or {}
    for image in snapshot.get('image_results', ()):
        if any(reference.get('message_id') == alias.get('message_id') and
               reference.get('content_index') == alias.get('content_index') for alias in image.get('displayed_in', ())):
            return {'message_id': image['source_message_id'], 'content_index': image['content_index']}
    return copy.deepcopy(reference)


def mark_viewed(runtime, reference):
    """Host-attested model image inputs and successful native reads share this receipt."""
    if (runtime.checkpoint_base.get('_request_context_v1') or {}).get('image_reference_version') != 1:
        return
    identity = scope(runtime)
    journal = runtime.checkpoint_base.get(KEY) or {}
    if journal.get('scope') != identity:
        journal = {'scope': identity, 'images': [], 'cursors': {}, 'pages': {}}
        runtime.checkpoint_base[KEY] = journal
    reference = canonical_reference(runtime, reference)
    viewed = journal.setdefault('viewed', [])
    if reference not in viewed:
        viewed.append(copy.deepcopy(reference))


def was_viewed(runtime, reference):
    journal = runtime.checkpoint_base.get(KEY) or {}
    if (runtime.checkpoint_base.get('_request_context_v1') or {}).get('image_reference_version') != 1:
        return False
    return journal.get('scope') == scope(runtime) and canonical_reference(runtime, reference) in journal.get('viewed', [])


def requires_view(runtime, reference, *, model_view=False):
    snapshot = runtime.checkpoint_base.get('_request_context_v1') or {}
    reference = canonical_reference(runtime, reference)
    if model_view:
        from .image_visual_input import includes
        if includes(runtime, reference):
            return False
    if snapshot.get('image_provenance_version') == 1:
        current = [image['reference'] for row in snapshot.get('requests', ())
                   if row['message_id'] == snapshot['input_message_id'] for image in row.get('images', ())]
        return reference not in current and not was_viewed(runtime, reference)
    known = [image['reference'] for row in snapshot.get('requests', ()) for image in row.get('images', ())]
    known += [{'message_id': image['source_message_id'], 'content_index': image['content_index']}
              for image in snapshot.get('image_results', ())]
    return reference not in known and not was_viewed(runtime, reference)


def selection_purpose(runtime):
    from .request_window import projection as request_projection
    projection = request_projection(runtime) or (getattr(runtime, 'checkpoint_base', {}) or {}).get('_ecom_workflow') or {}
    return (projection['intent'], (projection.get('request_context') or {}).get('image_action')) if projection.get('intent') else None


def selection_arguments(reference, *, intent='explain', image_action='display_existing'):
    frame = {'focus': 'current_request' if intent in {'write', 'write_then_submit_images'} else 'selected_target', 'target': {'kind': 'image', 'basis': 'explicit_reference',
        'source_message_id': reference['message_id'], 'content_index': reference['content_index']}}
    frame['image_action'] = image_action if intent == 'explain' else None
    return {'intent': intent, 'request_context': frame}


def selection_recovery(runtime, reference, *, intent='explain', image_action='display_existing', model_view=False):
    needed = requires_view(runtime, reference, model_view=model_view)
    return {'action': 'view_before_selection' if needed else 'select_target',
        'tool': 'read_conversation_text' if needed else 'resolve_request_target',
        'arguments': {'view_image': reference} if needed else selection_arguments(reference, intent=intent, image_action=image_action)}


def selection_metadata(runtime, images, *, model_view=False):
    intent, action = selection_purpose(runtime) or ('explain', 'display_existing')
    for image in images:
        ref = image.get('reference') or {'message_id': image['source_message_id'], 'content_index': image['content_index']}
        image['reference'] = ref
        needed = requires_view(runtime, ref, model_view=model_view)
        image['requires_view_before_selection'] = needed
        image['next_step'] = selection_recovery(runtime, ref, intent=intent, image_action=action, model_view=model_view)
        image.pop('selection_arguments', None)
        if not needed:
            image['selection_arguments'] = selection_arguments(ref, intent=intent, image_action=action)
            if intent not in {'explain', 'edit', 'generate', 'write', 'write_then_submit_images'}:
                image.pop('selection_arguments')
                image['next_step'] = {'action': 'retain_current_action',
                    'instruction': '该候选是图片来源；保留当前稿件目标与动作，需要改变时先核对用户要求。'}


def origin_metadata(db, context, native, row, part):
    # Re-displaying an upload does not turn it into a newly generated image.
    from services.handlers.chat_context.image_sources import content_parts
    seen = set()
    for _ in range(16):
        if not part.get('source_message_id'):
            break
        pair = (part['source_message_id'], part.get('source_content_index'))
        if pair in seen or type(pair[1]) is not int or pair[1] < 0:
            raise ValueError('CHAT_IMAGE_PROVENANCE_INVALID')
        seen.add(pair)
        row = native._message(pair[0])  # Same conversation, subject and frozen revision.
        parts = content_parts(row.get('content'))
        if pair[1] >= len(parts) or not isinstance(parts[pair[1]], dict) or parts[pair[1]].get('type') != 'image':
            raise ValueError('CHAT_IMAGE_PROVENANCE_INVALID')
        part = parts[pair[1]]
    if part.get('source_message_id'):
        raise ValueError('CHAT_IMAGE_PROVENANCE_INVALID')
    source = row if row['role'] == 'user' else None
    native_task = None
    if row['role'] == 'assistant' and part.get('task_id'):
        task = db.table('tasks').select('id,user_id,org_id,conversation_id,request_params').eq(
            'id', str(part['task_id'])).maybe_single().execute().data
        if task and (str(task['user_id']), task.get('org_id'), str(task['conversation_id'])) == (
                str(context.actor_user_id), context.org_id, str(context.conversation_id)):
            parent_id = ((task.get('request_params') or {}).get('_media_request_v1') or {}).get('origin', {}).get('parent_task_id')
            if parent_id:
                parent = db.table('tasks').select('input_message_id,user_id,org_id,conversation_id').eq(
                    'id', str(parent_id)).maybe_single().execute().data
                if parent and (str(parent['user_id']), parent.get('org_id'), str(parent['conversation_id'])) == (
                        str(context.actor_user_id), context.org_id, str(context.conversation_id)) and parent.get('input_message_id'):
                    source = native._message(str(parent['input_message_id']))
            native_task = str(task['id'])
    number = None
    if source and source.get('context_revision') is not None:
        query = db.table('messages').select('id', count='exact').eq(
            'conversation_id', context.conversation_id).eq('role', 'user').eq('status', 'completed').gt(
            'context_revision', 0).lte('context_revision', source['context_revision'])
        query = query.eq('org_id', context.org_id) if context.org_id else query.is_('org_id', 'null')
        number = query.limit(1).execute().count
    from services.handlers.chat_context.image_sources import content_parts
    excerpt = '\n'.join(p.get('text', '') for p in content_parts((source or {}).get('content'))
                        if isinstance(p, dict) and p.get('type') == 'text')[:400]
    from services.image_provenance import kind
    return {'source_turn': number, 'generated_for_message_id': source.get('id') if source else None,
            'origin_kind': kind(part, row['role']),
            'original_reference': {'message_id': str(row['id']), 'content_index': pair[1]} if seen else None,
            'task_id': native_task, 'request_excerpt': excerpt}


def page(handler, request, context, runtime, arguments):
    from services.handlers.chat.content_tools import check_source_scope
    from services.handlers.chat_context.image_sources import content_parts
    from .image_resources import native_resolver

    check_source_scope(handler.db, context)
    value = ListImages.model_validate(arguments)
    identity = scope(runtime)
    old = runtime.checkpoint_base.get(KEY) or {}
    journal = copy.deepcopy(old) if old.get('scope') == identity else {
        'scope': identity, 'images': [], 'cursors': {}, 'pages': {}}
    position = None
    if value.cursor is not None:
        cursor = journal['cursors'].get(value.cursor)
        if not cursor or any(field in value.model_fields_set and cursor[field] != getattr(value, field)
                             for field in ('order', 'source')):
            raise ValueError('CHAT_IMAGE_HISTORY_CURSOR_INVALID')
        value = value.model_copy(update={'order': cursor['order'], 'source': cursor['source']})
        position = cursor['position']
    cache_key = json.dumps(value.model_dump(mode='json'), sort_keys=True)
    if cache_key in journal['pages']:
        from .reference_boundary import for_runtime, NativeContext
        reply = copy.deepcopy(journal['pages'][cache_key])
        selection_metadata(runtime, reply['images'])
        return for_runtime(runtime).context(NativeContext(reply))
    native = native_resolver(handler, request, context)
    def query():
        builder = handler.db.table('messages').select(
            'id,role,content,context_revision,conversation_id,org_id,status,message_kind').eq(
            'conversation_id', context.conversation_id).eq('message_kind', 'conversation').eq(
            'status', 'completed').in_('role', ['user', 'assistant']).lte('context_revision', native.base_revision).gt('context_revision', 0)
        return builder.eq('org_id', context.org_id) if context.org_id else builder.is_('org_id', 'null')
    descending = value.order == 'newest_first'
    if position:
        # The native QueryBuilder supports flat filters, not nested OR/AND.
        # Read the anchor, remaining equal-revision rows, then later revisions.
        rows = query().eq('context_revision', position[0]).eq('id', position[1]).execute().data or []
        if not rows:
            raise ValueError('CHAT_IMAGE_HISTORY_CURSOR_INVALID')
        same = query().eq('context_revision', position[0])
        same = same.lt('id', position[1]) if descending else same.gt('id', position[1])
        rows += same.order('id', desc=descending).limit(100 - len(rows)).execute().data or []
        if len(rows) < 100:
            later = query()
            later = later.lt('context_revision', position[0]) if descending else later.gt('context_revision', position[0])
            rows += later.order('context_revision', desc=descending).order('id', desc=descending).limit(100 - len(rows)).execute().data or []
    else:
        rows = query().order('context_revision', desc=descending).order('id', desc=descending).limit(100).execute().data or []
    results = []
    end = position
    stopped = False
    for row in rows:
        # Recheck each row using the same native scope/version guard as view/edit.
        row = native._message(str(row['id']))
        parts = content_parts(row.get('content'))
        for index, part in enumerate(parts):
            key = [row['context_revision'], str(row['id']), index]
            if position and key[:2] == position[:2] and index <= position[2]:
                continue
            end = key
            if not isinstance(part, dict) or part.get('type') != 'image' or part.get('failed'):
                continue
            source = 'generated' if row['role'] == 'assistant' else 'uploaded'
            if value.source not in {'all', source}:
                continue
            if value.source == 'generated' and part.get('source_message_id'):
                continue  # A re-display is not another generation result.
            if len(results) >= value.limit:
                stopped = True
                break
            locator = {'message_id': str(row['id']), 'content_index': index}
            # Source identity proof is server-owned, not a model-written caption.
            checksum = hashlib.sha256(json.dumps(part, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            from services.image_provenance import kind
            origin = origin_metadata(handler.db, context, native, row, part)
            locator = origin.get('original_reference') or locator
            item = {'source_message_id': locator['message_id'], 'content_index': locator['content_index'],
                'source_sha256': checksum, 'context_revision': row['context_revision'],
                'provenance': kind(part, row['role']),
                'reference': locator,
                'name': part.get('name'), 'width': part.get('width'), 'height': part.get('height'),
                **origin}
            if not any(i['reference'] == locator for i in journal['images']):
                journal['images'].append(item)
            results.append(item)
        if stopped:
            break
        # Include messages with no content so sparse history can still advance.
        end = [row['context_revision'], str(row['id']), len(parts) - 1]
    has_more = stopped or len(rows) == 100
    if stopped:
        # The first unreturned candidate must remain on the next page.
        end[2] -= 1
    token = None
    if has_more and end:
        token = 'cursor_' + hashlib.sha256(json.dumps([identity, value.order, value.source, end]).encode()).hexdigest()[:32]
        journal['cursors'][token] = {'order': value.order, 'source': value.source, 'position': end}
    runtime.checkpoint_base[KEY] = journal
    selection_metadata(runtime, results)
    reply = {'ok': True, 'images': results, 'has_more': bool(token), 'next_cursor': token,
             'scope': 'authenticated_frozen_conversation_history', 'metadata_is_visual_confirmation': False,
             'next_step': {'action': 'follow_candidate_next_step',
                 'instruction': '按用户指向核对候选，执行该候选next_step中的tool和arguments，不默认第一张。需要核图时先view_image，'
                                '再单独选择具体目标，保持explain和display_existing。查询成功尚未展示，目标绑定后后台可自动交付。'
                                '无候选且has_more=true继续翻页；无法确定时用respond_without_execution澄清。'}}
    journal['pages'][cache_key] = reply
    runtime.checkpoint_base[KEY] = journal
    from .reference_boundary import for_runtime, NativeContext
    return for_runtime(runtime).context(NativeContext(reply))
