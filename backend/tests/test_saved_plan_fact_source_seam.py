"""Free host replay verifies the actual source factory before paid fact review."""
import json
import os
import socket

import pytest
from services.adapters.dashscope.chat_adapter import DashScopeAPIError
from tests.test_original_skill_paid_probe import test_original_skill_real_planning_without_image_dispatch as apply_recorded_output

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not os.getenv('ORIGINAL_SKILL_SOURCE_REHEARSAL_LEDGER'),reason='private saved reference responses required')]


async def test_saved_plan_reaches_hash_bound_fact_review_without_database_or_network(monkeypatch,tmp_path):
    monkeypatch.delenv('ORIGINAL_SKILL_PAID_APPROVED',raising=False)
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_REPLAY_LEDGER',os.environ['ORIGINAL_SKILL_SOURCE_REHEARSAL_LEDGER'])
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_EVIDENCE_DIR',str(tmp_path))
    attempts=[]
    def deny(*_args,**_kwargs):
        attempts.append(True)
        raise AssertionError('No external socket allowed')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)
    with pytest.raises(DashScopeAPIError,match='SAVED_RESPONSE_EXHAUSTED'):
        await apply_recorded_output('reference-image-prompts',monkeypatch,tmp_path)
    record=json.loads((tmp_path/'reference-image-prompts.json').read_text())
    pending=record['plan_reviews']['pending']
    assert len(pending['sources']['resolved'])==2
    assert [s['content_sha256'] for s in pending['sources']['resolved']]==[
        'c73d3ea0a8c7cf4e4b8d4c65918eec794ed5aba12765904fe0470cd779765d81',
        '52f7d869de0d2c5cf05d706bc9c9f1f83c0e0c72ee52fa402895d884b626bda3']
    artifact=next(a for a in record['task_state']['artifacts'] if a['id']==pending['artifact_id'])
    assert 'reference-prompts-v1' in artifact['validations']
    assert len(json.loads(artifact['inline_text'])['image_plans'])==5
    assert record['business_dispatch_count']==0 and attempts==[]
    assert record['task_state']['status']=='active'  # Pending review is not completion.


@pytest.mark.skipif(not os.getenv('ORIGINAL_SKILL_EXACT_SCOPE'),reason='saved actual scope required')
async def test_frozen_actual_scope_reuses_full_received_plan_prefix_by_exact_request_hash(monkeypatch,tmp_path):
    from pathlib import Path
    import shutil
    scope=Path(os.environ['ORIGINAL_SKILL_EXACT_SCOPE'])
    shutil.copyfile(scope,tmp_path/'reference-image-prompts.scope.json')
    monkeypatch.delenv('ORIGINAL_SKILL_PAID_APPROVED',raising=False)
    monkeypatch.delenv('ORIGINAL_SKILL_PAID_REPLAY_LEDGER',raising=False)
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_EXACT_REPLAY_LEDGER',os.environ['ORIGINAL_SKILL_EXACT_LEDGER'])
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_EVIDENCE_DIR',str(tmp_path))
    attempts=[]
    def deny(*_args,**_kwargs):
        attempts.append(True);raise AssertionError('Exact replay may not access external sockets')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)
    with pytest.raises(DashScopeAPIError,match='SAVED_RESPONSE_EXHAUSTED'):
        await apply_recorded_output('reference-image-prompts',monkeypatch,tmp_path)
    record=json.loads((tmp_path/'reference-image-prompts.json').read_text())
    books=[os.environ['ORIGINAL_SKILL_EXACT_LEDGER'],os.environ['ORIGINAL_SKILL_PAID_PRIOR_LEDGER']]
    received={c['body_sha256'] for book in books for c in json.loads(Path(book).read_text())['calls'] if c['status']=='received'}
    hashes=[v['body_sha256'] for v in record['request_views']]
    assert len(hashes)>=7 and all(h in received for h in hashes[:-1]) and hashes[-1] not in received
    # Stop precisely at the changed repair feedback. Existing actual draft is
    # reused unchanged; the new repair request is not sent to a live provider.
    original=json.loads((scope.parent/'reference-image-prompts.json').read_text())
    original_drafts={a['sha256'] for a in original['task_state']['artifacts'] if 'reference-prompts-v1' in a['validations']}
    assert original_drafts & {a['sha256'] for a in record['task_state']['artifacts']}
    assert record['plan_reviews']['attempts'][-1]['status']=='uncertain'
    assert record['business_dispatch_count']==0 and attempts==[]
