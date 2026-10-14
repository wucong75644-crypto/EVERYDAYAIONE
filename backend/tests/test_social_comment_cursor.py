"""Public pagination with real disposable-DB checkpoints, no platform/model IO."""
from uuid import uuid4
import pytest
from services.agent.agent_reach.contracts import ReachRequest, ReachResult, ReachItem, ReachError
from services.social_ops.comment_cursor import comment_chunk, MAX_COMMENT_CHUNKS, unique_comment_observations
from services.social_ops.contracts import ResearchCreate
from tests.test_social_ops_postgres import stores, worker

URL = 'https://www.xiaohongshu.com/explore/publicsample?xsec_token=fixture'


def response(page):
    # Overlapping platform comment ID across pages; long JSON also splits mid-item.
    ids = range(0, 10) if page == 1 else range(9, 19)
    return ReachResult('xiaohongshu', 'fixture', [
        ReachItem('评论', URL + '#comment-' + str(i), '{"id":"' + str(i) + '","content":"' + '文' * 2100 + '"}', 'comment') for i in ids
    ], pagination={'page': page, 'next_page': page + 1, 'loaded_count': 30, 'returned_count': 10, 'platform_has_more': True})


def test_chunk_cycle_limit_and_invalid_fragment():
    item = {'request': {'cursor': 'a'}, 'result_meta': {'cursor_state': {'chunks': 1, 'seen': ['old']}}}
    result = response(1).to_agent_result()
    result.metadata['pagination'].update(has_more_text=True, next_cursor='old')
    with pytest.raises(ReachError, match='未前进'):
        comment_chunk(item, result)
    item['result_meta']['cursor_state']['chunks'] = MAX_COMMENT_CHUNKS - 1
    with pytest.raises(ReachError, match='上限'):
        comment_chunk(item, result)
    item['result_meta']['cursor_state'] = {'chunks': 1, 'pending': {'url': 'https://www.xiaohongshu.com/explore/other#comment-x', 'content': 'x'}}
    with pytest.raises(ReachError, match='断点不一致'):
        comment_chunk(item, response(1).to_agent_result())


def test_two_pages_checkpoint_reclaim_fragments_and_dedupe(stores):
    s = stores
    job = s.repo.create_job(ResearchCreate(client_request_id=uuid4(), connection_id=s.connection['id'], profile_id=s.profile['profile_id'], keywords=[], sample_urls=[URL], max_items=1, comment_pages=2))
    w = worker(s)
    parent, read = w.claim(job['id'])
    w.settle(parent, read, ReachResult('xiaohongshu', 'fixture', [ReachItem('样本', URL, '正文')]).to_agent_result())
    old_claim = None
    chunks = 0
    pages = []
    while claimed := w.claim(job['id']):
        parent, item = claimed
        assert item['action'] == 'read_comments' and item['page'] in (1, 2)
        if item['request'].get('cursor') and old_claim is None:
            # Restart after committed cursor checkpoint, then reclaim expired lease.
            with s.repo.cursor() as c:
                c.execute("UPDATE social_research_items SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (item['id'],))
            old_claim = (parent, item)
            w = worker(s)
            parent, renewed = w.claim(job['id'])
            assert renewed['request'] == item['request']
            w.settle(*old_claim, response(item['page']).to_agent_result(ReachRequest(**item['request'])))
            item = renewed
        pages.append(item['page'])
        result = response(item['page']).to_agent_result(ReachRequest(**item['request']))
        w.settle(parent, item, result)
        # Duplicate/late settlement must not append fragments or duplicate rows.
        w.settle(parent, item, result)
        chunks += 1
        assert chunks <= 2 * MAX_COMMENT_CHUNKS
    saved = s.repo.sources(str(job['id']))
    comments = [r for r in saved if r['coverage']['content_kind'] == 'comment']
    assert len(comments) == 20
    assert len({r['source_key'] for r in comments}) == 19
    assert len(unique_comment_observations(comments)) == 19
    assert len(unique_comment_observations(comments + comments)) == 19
    assert all(r['body'].endswith('"}') and r['body'].count('文') == 2100 for r in comments)
    assert pages == sorted(pages) and set(pages) == {1, 2}
    assert s.repo.job(str(job['id']))['status'] == 'completed'
    for item in s.repo.job(str(job['id']))['items']:
        if item['action'] == 'read_comments':
            assert item['result_meta']['pagination']['has_more_text'] is False
            assert item['result_meta']['cursor_state']['pending'] is None
            assert item['result_meta']['saved_sources'] == 10
    with pytest.raises(ReachError):
        s.foreign.sources(str(job['id']))


def test_budget_wait_and_content_change_preserve_cursor_without_restart(stores):
    from types import SimpleNamespace
    s = stores
    w = worker(s)
    job = s.repo.create_job(ResearchCreate(client_request_id=uuid4(), connection_id=s.connection['id'], profile_id=s.profile['profile_id'], keywords=[], sample_urls=[URL], max_items=1, comment_pages=1))
    parent, read = w.claim(job['id'])
    w.settle(parent, read, ReachResult('xiaohongshu', 'fixture', [ReachItem('样本', URL, '正文')]).to_agent_result())
    parent, first = w.claim(job['id'])
    w.settle(parent, first, response(1).to_agent_result(ReachRequest(**first['request'])))
    parent, second = w.claim(job['id'])
    checkpoint = second['request']['cursor']
    from datetime import datetime, timezone
    w.settle(parent, second, SimpleNamespace(status='error', error_message='ACCESS_BUDGET_WAIT', metadata={'next_eligible_at': datetime.now(timezone.utc).isoformat()}))
    parent, retry = w.claim(job['id'])
    assert retry['request']['cursor'] == checkpoint
    assert retry['result_meta']['cursor_state'] == second['result_meta']['cursor_state']
    before = [(r['id'], r['body_hash']) for r in s.repo.sources(str(job['id']))]
    w.settle(parent, retry, SimpleNamespace(status='error', error_message='CONTENT_CHANGED', metadata={}))
    assert w.claim(job['id']) is None
    saved = s.repo.job(str(job['id']))
    assert saved['status'] == 'needs_input' and saved['last_error'] == 'CONTENT_CHANGED'
    assert [(r['id'], r['body_hash']) for r in s.repo.sources(str(job['id']))] == before
    with s.repo.cursor() as c:
        c.execute('SELECT request,result_meta FROM social_research_items WHERE id=%s', (retry['id'],))
        paused = c.fetchone()
    assert paused['request']['cursor'] == checkpoint
    assert paused['result_meta']['cursor_state'] == retry['result_meta']['cursor_state']


def test_dedup_keeps_latest_observation_and_separates_posts():
    rows = [
        {"source_key": "xiaohongshu:one#comment-a", "body": "latest"},
        {"source_key": "xiaohongshu:one#comment-a", "body": "old"},
        {"source_key": "xiaohongshu:two#comment-a", "body": "different post"},
    ]
    assert unique_comment_observations(rows) == [rows[0], rows[2]]
