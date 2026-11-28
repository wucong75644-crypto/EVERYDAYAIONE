"""A factual, turn-scoped view of requests and their actual media objects.

No prose classification lives here. Client quotes and model selections retain
their provenance; neither is promoted to a new upload or user authorization.
"""
from __future__ import annotations

import copy


def source_for(group, requests):
    creator = group.get('creation_task_id') or group['root_task_id']
    return next((row for row in requests if row.get('task_id', row['root_task_id']) == creator), None)


def project(snapshot, groups, resolution):
    requests = []
    source_rows = snapshot['requests']
    by_message = {row['message_id']: row for row in source_rows}
    by_image = {(image['source_message_id'], image['content_index']): image
                for image in snapshot.get('image_results', ())}

    def image_entry(image):
        reference = image['reference']
        return {**copy.deepcopy(image), 'target_fields': {'kind': 'image',
                'source_message_id': reference['message_id'], 'content_index': reference['content_index']}}

    def origin(reference):
        source = by_message.get(reference.get('message_id'))
        generated = by_image.get((reference.get('message_id'), reference.get('content_index')))
        if source is not None:
            available = any(i['reference']['content_index'] == reference.get('content_index') for i in source.get('images', ()))
            return {'kind': 'user_message' if available else 'reference_not_found_in_message',
                    'message_id': source['message_id'], 'turn_number': source.get('turn_number')}
        if generated is not None:
            return {'kind': generated.get('origin_kind', 'generated_result'), 'message_id': generated['source_message_id'],
                    'for_message_id': generated.get('generated_for_message_id'),
                    'turn_number': generated.get('original_turn', generated.get('generated_for_turn'))}
        return {'kind': 'history_not_in_recent_index'}

    saved = []
    for group in groups:
        row = source_for(group, source_rows)
        entry = {key: copy.deepcopy(value) for key, value in group.items() if key != 'item_inputs'}
        entry['source_request'] = ({'message_id': row['message_id'], 'turn_number': row.get('turn_number'),
                                    'text_excerpt': (row.get('text_excerpt') or '')[:200],
                                    'text_requires_read': row.get('text_requires_read', False) or len(row.get('text_excerpt') or '') > 200} if row else None)
        # Preserve user-authored titles. Historical labels are never statements
        # about the current turn; source_request is the authoritative provenance.
        entry['title_provenance'] = 'saved_document_title_not_current_turn'
        if row:
            entry['display_label'] = f"第{row['turn_number']}轮：{row['text_excerpt'][:100]}" if row.get('turn_number') else row['text_excerpt'][:100]
        else:
            entry['display_label'] = '历史保存稿；来源轮次未在最近索引中'
        entry['document_title'] = entry['title']
        entry['title'] = entry['display_label']
        for item in entry['items']:
            published = [i for i in snapshot.get('image_results', ()) if i.get('task_id') and i['task_id'] == item.get('image_task_id')]
            if published:
                item['operation_status'] = item.pop('image_status', None)
                item['result_status'] = 'published'
                item['published_results'] = copy.deepcopy(published)
        saved.append(entry)

    for row in source_rows:
        entry = copy.deepcopy(row)
        entry['images'] = [image_entry(i) for i in row.get('images', ())]
        entry['new_uploads'] = [image_entry(i) for i in row.get('images', ()) if not i.get('client_reference')]
        entry['quoted_images'] = [{**image_entry(i), 'reference_provenance': 'client_supplied_quote',
                                   'original_source': origin(i['client_reference'])}
                                  for i in row.get('images', ()) if i.get('client_reference')]
        entry['selected_images'] = [{**copy.deepcopy(i), 'reference_provenance': 'model_selection',
                                    'original_source': origin(i.get('reference', {}))}
                                   for i in row.get('selected_images', ())]
        entry['saved_contents'] = [{key: g[key] for key in ('artifact_id', 'revision', 'title')}
                                  for g in saved if (g.get('source_request') or {}).get('message_id') == row['message_id']]
        entry['generated_results'] = [copy.deepcopy(i) for i in snapshot.get('image_results', ())
                                      if i.get('generated_for_message_id') == row['message_id']]
        entry['chat_turn_status'] = entry.pop('task_status', None)
        requests.append(entry)

    current = next((r for r in requests if r['message_id'] == snapshot['input_message_id']), None)
    return {'recent_requests': requests, 'saved_groups': saved, 'current_turn': current}
