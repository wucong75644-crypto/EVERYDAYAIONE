"""Historic resources can be paged, inspected and selected without handle drift."""
import copy
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import image_history as history, request_resolution as rr
from services.task_execution.image_reference_catalog import for_runtime
from tests.test_request_resolution import setup_case


class Query:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.ordering = []
        self.count = False
        self.maximum = 100

    def select(self, *args, **kwargs): self.count = kwargs.get('count') == 'exact'; return self
    def eq(self, key, value): self.rows = [r for r in self.rows if r.get(key) == value]; return self
    def is_(self, key, value): return self.eq(key, None)
    def in_(self, key, values): self.rows = [r for r in self.rows if r.get(key) in values]; return self
    def lte(self, key, value): self.rows = [r for r in self.rows if r.get(key) is not None and r[key] <= value]; return self
    def gt(self, key, value): self.rows = [r for r in self.rows if r.get(key) is not None and r[key] > value]; return self
    def lt(self, key, value): self.rows = [r for r in self.rows if r.get(key) is not None and r[key] < value]; return self
    def order(self, key, desc=False): self.ordering.append((key, desc)); return self
    def limit(self, value): self.maximum = value; return self
    def or_(self, expression):
        op, revision, message_id = re.search(r'context_revision\.(lt|gt)\.(\d+),.*id\.(?:gte|lte)\.([a-f0-9-]+)', expression).groups()
        pair = (int(revision), message_id)
        self.rows = [r for r in self.rows if ((r['context_revision'], r['id']) <= pair if op == 'lt'
                                            else (r['context_revision'], r['id']) >= pair)]
        return self
    def execute(self):
        for key, desc in reversed(self.ordering): self.rows.sort(key=lambda r: r[key], reverse=desc)
        return SimpleNamespace(data=self.rows[:self.maximum], count=len(self.rows) if self.count else None)


def case(monkeypatch, images=35, sparse=False):
    runtime, prepared, handler, request, rows = setup_case()
    snapshot = rr.context(runtime)
    snapshot['image_reference_version'] = 1
    context = prepared.execution_context
    messages = [{'id': str(uuid4()), 'role': 'assistant', 'content': [{'type': 'image', 'name': f'图片{i}'}],
                 'context_revision': i + 1, 'conversation_id': context.conversation_id,
                 'org_id': context.org_id, 'status': 'completed', 'message_kind': 'conversation'} for i in range(images)]
    if sparse:
        for row in messages[:-1]: row['content'] = [{'type': 'text', 'text': '说明'}]
    handler.db = SimpleNamespace(table=lambda name: Query(messages if name == 'messages' else []))
    def message(mid):
        row = next(r for r in messages if r['id'] == mid)
        if row['context_revision'] > 1000: raise PermissionError('CHAT_SOURCE_DENIED')
        return copy.deepcopy(row)
    native = SimpleNamespace(base_revision=1000, _message=message)
    monkeypatch.setattr('services.task_execution.image_resources.native_resolver', lambda *a: native)
    monkeypatch.setattr('services.handlers.chat.content_tools.check_source_scope', lambda *a: None)
    return runtime, handler, request, context, messages


@pytest.mark.parametrize('order', ['oldest_first', 'newest_first'])
def test_pages_reach_old_images_and_preserve_every_existing_handle(monkeypatch, order):
    runtime, handler, request, context, rows = case(monkeypatch)
    before = copy.deepcopy(for_runtime(runtime).references)
    result = history.page(handler, request, context, runtime, {'order': order, 'source': 'generated', 'limit': 10})
    first = copy.deepcopy(result)
    all_refs = []
    while True:
        all_refs += [i['image_ref'] for i in result['images']]
        assert all(for_runtime(runtime).references[k] == v for k, v in before.items())
        if not result['has_more']: break
        result = history.page(handler, request, context, runtime, {'order': order, 'source': 'generated',
            'limit': 10, 'cursor': result['next_cursor']})
    assert len(all_refs) == len(set(all_refs)) == 35
    for key, value in before.items(): assert for_runtime(runtime).references[key] == value
    assert history.page(handler, request, context, runtime, {'order': order, 'source': 'generated', 'limit': 10}) == first
    chosen = for_runtime(runtime).decode({'kind': 'image', 'basis': 'explicit_reference', 'image_ref': all_refs[0]})
    from services.task_execution.contracts import TaskError
    with pytest.raises(TaskError, match='REQUEST_TARGET_IMAGE_REQUIRES_VIEW'):
        rr.freeze(runtime, request, chosen)
    history.mark_viewed(runtime, {'message_id': chosen['source_message_id'], 'content_index': chosen['content_index']})
    frozen = rr.freeze(runtime, request, chosen)
    assert frozen['target']['source_message_id'] == rows[0 if order == 'oldest_first' else -1]['id']


def test_sparse_history_advances_empty_pages_and_cursor_is_bound_to_input(monkeypatch):
    runtime, handler, request, context, rows = case(monkeypatch, images=105, sparse=True)
    reply = history.page(handler, request, context, runtime, {})
    assert reply['images'] == [] and reply['has_more']
    next_page = history.page(handler, request, context, runtime, {'cursor': reply['next_cursor']})
    assert len(next_page['images']) == 1 and not next_page['has_more']
    rr.context(runtime)['input_message_id'] = str(uuid4())
    with pytest.raises(ValueError, match='CHAT_IMAGE_HISTORY_CURSOR_INVALID'):
        history.page(handler, request, context, runtime, {'cursor': reply['next_cursor']})
    assert history.recovered(runtime) == []


def test_history_tool_schema_and_no_implicit_selection(monkeypatch):
    runtime, handler, request, context, _ = case(monkeypatch)
    previous = copy.deepcopy(rr.binding(runtime))
    Draft202012Validator(history.SCHEMA['function']['parameters']).validate({'order': 'oldest_first', 'source': 'generated', 'limit': 10})
    result = history.page(handler, request, context, runtime, {})
    assert result['metadata_is_visual_confirmation'] is False
    assert rr.binding(runtime) == previous
    with pytest.raises(ValueError, match='CHAT_IMAGE_HISTORY_CURSOR_INVALID'):
        history.page(handler, request, context, runtime, {'cursor': 'invented'})


def test_cursor_alone_restores_structured_query_but_explicit_conflict_is_rejected(monkeypatch):
    runtime, handler, request, context, _ = case(monkeypatch)
    first = history.page(handler, request, context, runtime, {'source': 'generated', 'limit': 2})
    second = history.page(handler, request, context, runtime, {'cursor': first['next_cursor'], 'limit': 2})
    assert len(second['images']) == 2 and not {i['image_ref'] for i in first['images']} & {i['image_ref'] for i in second['images']}
    with pytest.raises(ValueError, match='CHAT_IMAGE_HISTORY_CURSOR_INVALID'):
        history.page(handler, request, context, runtime, {'cursor': first['next_cursor'], 'source': 'uploaded'})


def test_candidate_selection_examples_and_cached_view_requirements_match_native_rules(monkeypatch):
    runtime, handler, request, context, _ = case(monkeypatch)
    result = history.page(handler, request, context, runtime, {'source': 'generated', 'limit': 2})
    schema = for_runtime(runtime).schema(rr.selection_schema(runtime))['function']['parameters']
    assert result['next_step']['tool'] == 'resolve_request_target'
    for image in result['images']:
        Draft202012Validator(schema).validate(image['selection_arguments'])
        assert image['selection_arguments']['request_context']['target']['image_ref'] == image['image_ref']
        assert image['requires_view_before_selection'] is True
    selected = result['images'][0]
    native = for_runtime(runtime).decode(selected['reference'])
    history.mark_viewed(runtime, native)
    again = history.page(handler, request, context, runtime, {'source': 'generated', 'limit': 2})
    assert again['images'][0]['requires_view_before_selection'] is False
    assert again['images'][1]['requires_view_before_selection'] is True
    assert rr.binding(runtime) is None  # Metadata and view proof never choose a target.
