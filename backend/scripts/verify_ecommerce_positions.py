"""Opt-in real-model comparison. Explicit disposable PostgreSQL; no application DB.

Set CHAT_IMAGE_TEST_ADMIN_DSN to the guarded local test cluster and pass --output.
Only selected provider credentials are read; no production database is opened.
"""
import argparse
import asyncio
import base64
from contextlib import suppress
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused@127.0.0.1:1/isolated')
os.environ.setdefault('JWT_SECRET_KEY', 'local-ecommerce-verification-only')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def credentials(path):
    from dotenv import dotenv_values
    allowed = {'DASHSCOPE_API_KEY', 'KIE_API_KEY', 'KIE_BASE_URL', 'KIE_SHADOW_OVERSEAS_PROXY'}
    for key, value in dotenv_values(path).items():
        if key in allowed and value:
            os.environ[key] = value


async def compare(args):
    import psycopg
    from psycopg.types.json import Jsonb
    from core.local_db import LocalDBClient
    from services.detail_page_generation import page_owner
    from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner, PlannerStreamBudget
    from services.agent.image.ecommerce_planner.prompt_resources import plan_resources, resource_versions
    from services.agent.image.ecommerce_planner.contracts import source_id
    from services.agent.image.ecommerce_planner.inputs import source_bindings
    from tests.test_chat_image_lifecycle_postgres import isolated_db
    from tests.test_ecommerce_recovery_postgres import ecom_db
    from tests.test_detail_page_generation_postgres import page_db, seed
    from tests.test_detail_page_delivery_recovery import delivery_db, opt_in
    from tests.test_ecommerce_format_recovery_postgres import format_db

    generator = isolated_db.__wrapped__()
    dsn = next(generator)  # The fixture rejects every host/port except the isolated Unix socket.
    try:
        ecom_db.__wrapped__(dsn)
        page_db.__wrapped__(dsn)
        delivery_db.__wrapped__(dsn)
        format_db.__wrapped__(dsn)
        migrations = Path(__file__).resolve().parents[1] / 'migrations'
        with psycopg.connect(dsn) as db:
            db.execute((migrations/'375_ecommerce_position_delivery.sql').read_text())
        files = [Path(path) for path in args.images]
        refs, urls = [], []
        for path in files:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            ref = {'file_id': 'verification_'+digest[:16], 'role':'product'}
            ref['source_id'] = source_id(ref)
            refs.append(ref)
            mime = 'image/png' if path.suffix.lower()=='.png' else 'image/jpeg'
            urls.append('data:'+mime+';base64,'+base64.b64encode(data).decode())
        manifest = {'images':[{'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
            'user_text':args.requirement,'count':args.count,'task_type':args.kind,'platform':args.platform,
            'model':'kimi-k3','reasoning':args.reasoning,'aspect_ratio':args.ratio,'resolution':'1K',
            'production_access':False,'image_generation_automatically_submitted':False}
        save(args.output/'input-manifest.json',manifest)

        async def execute(version):
            directory=args.output/version
            directory.mkdir(parents=True,exist_ok=True)
            user,project,run,plan=seed(dsn,args.count)
            opt_in(dsn,plan)
            snapshot={'messages':[{'source_id':'verification-input','parts':[{'content_index':0,'text':args.requirement}]}],
                'references':refs,'resolved_references':refs,'image_count':args.count,'task_type':args.kind,
                'platform':args.platform,'language':'zh-CN','target_size':{'aspect_ratio':args.ratio,'resolution':'1K'}}
            snapshot['source_bindings']=source_bindings(snapshot)
            profile= ('jd.v2' if version.endswith('v5') else 'jd.v1') if args.platform=='jd' else None
            with psycopg.connect(dsn) as db:
                db.execute('UPDATE ecom_image_plans SET input_snapshot=%s,target_size=%s,prompt_versions=%s,invocation_key=%s WHERE id=%s',
                    (Jsonb(snapshot),Jsonb(snapshot['target_size']),Jsonb(resource_versions(version,profile)),args.kind,plan))
            client=LocalDBClient(psycopg.conninfo.make_conninfo(dsn,user='everydayai'),min_size=1,max_size=4)
            owner=page_owner(client,user,None,project);owner.task_id=plan
            owner.cancellation_event=asyncio.Event();owner.execution_budget=PlannerStreamBudget(None,1800)
            config=SimpleNamespace(ecom_image_planning_model='kimi-k3',ecom_image_planning_reasoning=args.reasoning,
                ecom_analysis_transport='inline',ecom_analysis_json_output=True,
                ecom_image_planning_stage_timeout=300,wall_seconds=1800,
                ecom_image_planning_input_credits_per_million=1,ecom_image_planning_output_credits_per_million=1)
            planner=EcommerceImagePlanner(owner,execution_profile=config)
            row=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
            bodies,schema=plan_resources(row)
            original=planner._call
            calls=[]
            async def capture(row,lease,stage,prompt,messages,**kwargs):
                call_number=len(calls)+1
                record={'call':call_number,'stage':stage,'started_at':time.time(),**kwargs}
                calls.append(record)
                # Text and media are saved separately, preserving exact bytes in
                # original files and alias order without copying base64 N times.
                sent=deepcopy(messages)
                for message in sent:
                    if isinstance(message.get('content'),list):
                        n=0
                        for part in message['content']:
                            if part.get('type')=='input_image':
                                part['image_url']={'exact_bytes_from':str(files[n].resolve()),
                                    'sha256':manifest['images'][n]['sha256'],'transport':'data-url'}
                                n+=1
                save(directory/f'call-{call_number:02d}-input.json',{'messages':sent,'model':manifest['model'],
                    'reasoning':args.reasoning,'stage':stage})
                started=time.monotonic()
                try:
                    output,usage=await original(row,lease,stage,prompt,messages,**kwargs)
                    record.update(elapsed_seconds=round(time.monotonic()-started,3),usage=usage,status='completed')
                    save(directory/f'call-{call_number:02d}-output.json',{'output':output,'usage':usage})
                    return output,usage
                except Exception as exc:
                    from services.agent.image.ecommerce_planner.recovery import error_facts
                    code,category,_=error_facts(exc)
                    record.update(elapsed_seconds=round(time.monotonic()-started,3),status='error',
                        error_type=type(exc).__name__,code=code,category=category)
                    raise
                finally:
                    save(directory/'calls.json',calls)
            planner._call=capture
            async def renew():
                while True:
                    await asyncio.sleep(30)
                    lease=getattr(planner,'active_lease',None)
                    if lease and not await asyncio.to_thread(lambda:planner.scope.rpc('renew_detail_page_plan',{
                            'p_plan_id':plan,'p_lease_token':lease}).execute().data):
                        owner.cancellation_event.set()
                        return
            heartbeat=asyncio.create_task(renew())
            started=time.monotonic()
            try:
                result=await planner.execute(row,refs,urls,snapshot['messages'],schema,bodies)
                saved=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
                save(directory/'plan.json',saved)
                save(directory/'summary.json',{'status':saved['status'],'elapsed_seconds':round(time.monotonic()-started,3),
                    'stage':saved['current_stage'],'calls':calls,'error_message':result.error_message,
                    'image_count':len(saved.get('items') or []),'production_e2e':False})
                print(version,saved['status'],round(time.monotonic()-started,2),flush=True)
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):await heartbeat
                client.pool.close()
        await asyncio.gather(*(execute(version) for version in args.versions))
    finally:
        try:next(generator)
        except StopIteration:pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--credentials',type=Path,required=True)
    parser.add_argument('--images',nargs='+',required=True)
    parser.add_argument('--requirement',default='要可爱风格的背景，文字要和产品贴合的设计。')
    parser.add_argument('--count',type=int,default=6)
    parser.add_argument('--kind',choices=['main_images','detail_page'],default='main_images')
    parser.add_argument('--platform',default='taobao')
    parser.add_argument('--ratio',default='1:1')
    parser.add_argument('--reasoning',choices=['low','high'],default='low')
    parser.add_argument('--versions',nargs='+',default=['ecom-prompts.v4','ecom-prompts.v5'])
    args=parser.parse_args()
    if not 1<=args.count<=15:parser.error('count must be 1..15')
    credentials(args.credentials)
    asyncio.run(compare(args))


if __name__=='__main__':main()
