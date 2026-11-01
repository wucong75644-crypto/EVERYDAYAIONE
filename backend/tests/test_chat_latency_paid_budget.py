"""Budget safeguards without any provider calls or real credentials."""
import json
import pytest
from tests.fixtures.chat_latency_paid import PaidProbe


def probe(tmp_path,monkeypatch):
    monkeypatch.setenv('CHAT_LATENCY_PAID_APPROVED','30')
    monkeypatch.setenv('CHAT_LATENCY_PAID_LEDGER',str(tmp_path/'budget.json'))
    monkeypatch.setattr('dotenv.dotenv_values',lambda _: {'DASHSCOPE_API_KEY':'fake-local-only'})
    return PaidProbe()


def test_no_default_spending(monkeypatch):
    monkeypatch.delenv('CHAT_LATENCY_PAID_APPROVED',raising=False)
    with pytest.raises(RuntimeError,match='Explicit'):PaidProbe()


def test_unknown_acceptance_blocks_every_new_request(tmp_path,monkeypatch):
    p=probe(tmp_path,monkeypatch);p.reserve({'prompt':'one'})
    with pytest.raises(RuntimeError,match='uncertain'):p.reserve({'prompt':'different'})


def test_completed_response_is_preserved_and_exact_request_not_repeated(tmp_path,monkeypatch):
    p=probe(tmp_path,monkeypatch);i=p.reserve({'prompt':'one'});p.finish(i,'received',{'prompt_tokens':10},'original result')
    with pytest.raises(RuntimeError,match='Already attempted'):p.reserve({'prompt':'one'})
    assert json.loads(p.path.read_text())['calls'][0]['response_text']=='original result'


def test_four_calls_fit_budget_but_fifth_never_starts(tmp_path,monkeypatch):
    p=probe(tmp_path,monkeypatch)
    for n in range(4):
        i=p.reserve({'prompt':n});p.finish(i,'received',{})
    with pytest.raises(RuntimeError,match='exhausted'):p.reserve({'prompt':5})
    assert sum(c['reserved_credits'] for c in json.loads(p.path.read_text())['calls'])==20
