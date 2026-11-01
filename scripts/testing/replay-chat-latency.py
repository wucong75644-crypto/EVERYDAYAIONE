#!/usr/bin/env python3
"""Replay recorded native packets to the real page without another model call."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from tests.fixtures.chat_latency_browser import LocalLatencyBrowser


async def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
    records=[]
    for path in sorted(args.input.glob('*.json')):
        value=json.loads(path.read_text())
        if isinstance(value,list):records.extend(r for r in value if r.get('provider')=='real')
    if len(records)!=3:raise ValueError('Expected the three already-paid task records')
    browser=LocalLatencyBrowser();results=[]
    await browser.start()
    try:
        for record in records:
            packets=record['packets'];first=packets[0]['message']
            await browser.prepare(mode='recorded-real-result',scenario=record['scenario'],task_id=first['task_id'],
                message_id=first['message_id'],conversation_id=first['conversation_id'],batch_size=3)
            origin=next(r['monotonic_ns'] for r in record['trace'] if r['phase']=='enqueue.begin')
            started=time.monotonic()
            for packet in packets:
                target=(packet['at_ns']-origin)/1e9
                await asyncio.sleep(max(0,target-(time.monotonic()-started)))
                await browser.send(packet['message'])
            metrics=await browser.finish()
            if record['scenario'] in ('chat','image_analysis'):
                assert metrics['firstDomBeforeTerminal'] is True,metrics
            results.append({'scenario':record['scenario'],'transport':'recorded native Redis packets, original cadence; no provider calls', 'metrics':metrics})
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps({'scope':'page replay of preserved real-provider results; not a simultaneous production measurement','results':results},ensure_ascii=False,indent=2)+'\n')
    finally:await browser.close()


asyncio.run(main())
