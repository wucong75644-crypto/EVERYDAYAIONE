"""Recorded Max failures applied to current host; no claim of new model semantics."""
import json
import os
import socket
from pathlib import Path

import pytest

from tests.test_original_skill_paid_probe import test_original_skill_real_planning_without_image_dispatch as apply_recorded_output
from services.adapters.dashscope.chat_adapter import DashScopeAPIError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not os.getenv('ORIGINAL_SKILL_FAILURE_REPLAY_LEDGER'),reason='requires private recorded failures, no network fallback')]


@pytest.mark.parametrize('key',['reference-image-prompts','ecommerce-image-prompts'])
async def test_recorded_failure_stays_unfinished_and_retains_repair_evidence(key,monkeypatch,tmp_path):
    monkeypatch.delenv('ORIGINAL_SKILL_PAID_APPROVED',raising=False)
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_REPLAY_LEDGER',os.environ['ORIGINAL_SKILL_FAILURE_REPLAY_LEDGER'])
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_EVIDENCE_DIR',str(tmp_path))
    attempts=[]
    def deny(*args,**kwargs):
        attempts.append(True)
        raise AssertionError('External socket forbidden in recorded-output regression')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)
    with pytest.raises((DashScopeAPIError,AssertionError)) as error:
        await apply_recorded_output(key,monkeypatch,tmp_path)
    assert 'SAVED_RESPONSE_EXHAUSTED' in str(error.value) or 'Safety-only return is not final planning acceptance' in str(error.value)
    assert attempts==[]
    record=json.loads((tmp_path/(key+'.json')).read_text())
    assert record['business_dispatch_count']==0
    assert record['task_state'] is None
    assert record['requirement_review'] is not None
    if key=='reference-image-prompts':
        feedback=[json.loads(m['content'].split('\n')[-1]) for m in record['coverage_feedback']]
        assert feedback and all(f['task_initialized'] is False for f in feedback)
        assert any(f['observed_requirements'] for f in feedback)
        assert all(f['observed_requirements']['deliveries'] for f in feedback if f['observed_requirements'])
        review_calls=[v for v in record['observations'] if v['review']]
        assert len(review_calls)==1  # Same frozen source is reused across proposal corrections.
    else:
        outputs=[json.loads(m['content']) for m in record['tool_transcript']]
        assert any(v.get('code')=='TASK_CONTROL_INPUT_INVALID' and '1至4' in v['instruction'] for v in outputs)
        assert any(e['loc'][-1]=='evidence' and e['type']=='too_long' for v in outputs for e in v.get('errors',[]))
    initial=record['request_views'][0]['context_cost']
    assert initial['total_method_occurrences'][key]==1
    assert sum(initial['total_method_occurrences'].values())==1
    # The native engine must not advertise the competing server planner while
    # the original inline method owns planning. ERP and ordinary tools remain.
    assert 'plan_ecommerce_images' not in initial['tool_names']
    if key=='ecommerce-image-prompts':
        assert {'erp_agent','file_search','web_search'} <= set(initial['tool_names'])
    evidence=Path(os.getenv('SKILL_CONTEXT_EVIDENCE_DIR',tmp_path));evidence.mkdir(parents=True,exist_ok=True)
    (evidence/(key+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
