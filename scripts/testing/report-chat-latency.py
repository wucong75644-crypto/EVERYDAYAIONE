#!/usr/bin/env python3
"""Compare local protocol observations; never infer provider/production SLAs."""
import argparse
import csv
import json
from pathlib import Path
from statistics import median


def summarize(record):
    trace = record['trace']
    domains = {r['clock_domain'] for r in trace}
    attempts = {r['attempt'] for r in trace if r.get('attempt') is not None}
    if len(attempts) > 1: raise ValueError('Split attempts before comparing; pause time is not queue time')
    initial_attempt = attempts == {1}
    if len(domains) != 1:
        raise ValueError('Cannot subtract clocks from different hosts')
    def values(phase):
        return [r['monotonic_ns'] for r in trace if r['phase'] == phase]
    def first(phase):
        entries=values(phase)
        return min(entries) if entries else None
    def duration(start,end):
        a,b=first(start),first(end)
        return round((b-a)/1e6,3) if a is not None and b is not None and b>=a else None
    claim_row=next((r for r in trace if r['phase']=='claim.end'),{})
    rpc_start=claim_row.get('rpc_started_ns')
    persisted=first('enqueue.persisted')
    claim_end=first('claim.end')
    accepted = first('enqueue.begin')
    body = next((p['at_ns'] for p in record['packets'] if p['message']['type']=='message_chunk'),None)
    begin,end=values('verification.begin'),values('verification.end')
    model_end=values('model.end')
    return {
        'scenario':record['scenario'],'mode':record['mode'],
        'enqueue_rpc_ms':duration('enqueue.begin','enqueue.persisted'),
        # This includes scheduling/claim RPC; it is not the pure database wait.
        'enqueued_to_claim_ms':duration('enqueue.persisted','claim.end') if initial_attempt else None,
        'enqueue_return_to_claim_start_ms':round((rpc_start-persisted)/1e6,3) if initial_attempt and rpc_start and persisted and rpc_start>=persisted else None,
        'claim_call_ms':round((claim_end-rpc_start)/1e6,3) if rpc_start and claim_end else None,
        'executor_inputs_ms':duration('executor.begin','executor.inputs'),
        'context_fixture_ms':duration('context.begin','context.ready'),
        'model_first_body_ms':duration('model.begin','model.first_body'),
        'verification_ms':round(sum(b-a for a,b in zip(begin,end))/1e6,3) if len(begin)==len(end) else None,
        'actor_first_body_ms':duration('enqueue.begin','sink.first_body') if initial_attempt else None,
        'persist_first_body_ms':duration('sink.first_body','sink.first_persisted'),
        'redis_receiver_first_body_ms':round((body-accepted)/1e6,3) if body and accepted else None,
        'receiver_before_model_end':body < max(model_end) if body and model_end else None,
        'commit_total_ms':duration('enqueue.begin','terminal.commit.end') if initial_attempt else None,
        'attempt_to_commit_ms':duration('executor.begin','terminal.commit.end'),
        'body_chunks':sum(p['message']['type']=='message_chunk' for p in record['packets']),
        'client':record.get('client'),
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('input',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    results=[]
    providers=set()
    for path in sorted(args.input.glob('*.json')):
        records=json.loads(path.read_text())
        if not isinstance(records,list):continue
        providers.update(r.get('provider','controlled') for r in records)
        results.extend(summarize(r) for r in records)
    if not results:raise ValueError('No native protocol records')
    if len(providers)>1:raise ValueError('Do not mix real and controlled provider samples')
    rows=[]
    for scenario in sorted({r['scenario'] for r in results}):
        for mode in ('before','after'):
            samples=[r for r in results if r['scenario']==scenario and r['mode']==mode]
            if not samples:continue
            item={'scenario':scenario,'mode':mode,'samples':len(samples)}
            for key in samples[0]:
                if key in ('scenario','mode','client'):continue
                vals=[r[key] for r in samples if r[key] is not None]
                item[key]=round(median(vals),3) if vals and all(type(v) in (int,float) for v in vals) else (all(vals) if vals else None)
            rows.append(item)
    args.output.mkdir(parents=True,exist_ok=True)
    report={'scope':'local native queue/Actor/PG event store/cross-manager Redis/WebSocket/browser where client is present','provider':next(iter(providers)),
        'boundaries':[('history/evidence are controlled fixtures; the provider is real' if providers=={'real'} else 'history/context retrieval and model responses are controlled test seams'),'input/output messages are seeded, not production HTTP auth/admission','before replays the old blanket-hold condition on this candidate, not an archived production binary','browser measurements exist only in records with client field','enqueued_to_claim includes scheduling and claim RPC; paused duration is not inferred','single local fixed-response samples and cold-start differences are not production SLAs','never subtract server monotonic from client performance.now'],
        'records':results,'medians':rows}
    (args.output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    with (args.output/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps(rows,ensure_ascii=False))


if __name__=='__main__':main()
