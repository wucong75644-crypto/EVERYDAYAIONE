"""Submit real test images once, persist IDs immediately, and poll without resubmission."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_ecommerce_positions import credentials, save


async def run(args):
    import httpx
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr,level='WARNING')
    from core.config import get_settings
    from services.adapters.kie.client import KieClient
    from services.adapters.kie.image_adapter import KieImageAdapter
    from services.adapters.kie.models import CreateTaskRequest
    client=KieClient(get_settings().kie_api_key)
    query=client.query_task
    async def audited_query(task_id):
        response=await query(task_id)
        fields={'taskId','model','state','failCode','failMsg','costTime','createTime','completeTime','resultJson'}
        save(args.output/f'provider-{task_id}.json',{'code':response.code,'msg':response.msg,
            'data':{key:value for key,value in (response.data or {}).items() if key in fields}})
        return response
    client.query_task=audited_query
    adapter=KieImageAdapter(client,'gpt-image-2-5-sunburst-image-to-image')
    args.output.mkdir(parents=True,exist_ok=True)
    ledger_path=args.output/'receipts.json'
    ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    try:
        if args.action=='submit':
            plan=json.loads(args.plan.read_text())
            if plan.get('status')!='ready':raise ValueError('Plan must be ready')
            manifest=json.loads(args.manifest.read_text())
            retry_source=json.loads(args.retry_failed_from.read_text()) if args.retry_failed_from else None
            uploads_path=args.output.parent/'media-uploads.json'
            uploads=json.loads(uploads_path.read_text()) if uploads_path.exists() else {}
            urls=[]
            async with httpx.AsyncClient(proxy=None if args.upload_direct else os.getenv(KieClient.SHADOW_OVERSEAS_PROXY_ENV),
                    headers={'Authorization':'Bearer '+get_settings().kie_api_key},timeout=90) as transport:
                for item in manifest['images']:
                    path=Path(item['path']);data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
                    if digest!=item['sha256']:raise ValueError('Reference bytes changed')
                    if digest not in uploads:
                        url=await KieClient.upload_image_bytes(transport,data,
                            'image/png' if path.suffix=='.png' else 'image/jpeg','ecom-rebuild-'+digest[:16]+path.suffix)
                        uploads[digest]={'url':url,'path':str(path),'sha256':digest}
                        save(uploads_path,uploads)
                    urls.append(uploads[digest]['url'])
            for item in plan['items']:
                key=str(item['position'])
                previous=retry_source.get(key) if retry_source else None
                if retry_source is not None:
                    if not previous or previous.get('status')!='failed' or not previous.get('task_id'):continue
                    if previous.get('prompt_sha256')!=item['request_text_sha256']:
                        raise ValueError('Retry input is different from the definitively failed request')
                if key in ledger:continue  # Includes uncertain submissions: never silently resubmit.
                params=adapter._build_input_params(prompt=item['request_text'],image_urls=urls,
                    size=item['aspect_ratio'],output_format='png',resolution=plan['target_size']['resolution'])
                if hashlib.sha256(item['request_text'].encode()).hexdigest()!=item['request_text_sha256']:
                    raise ValueError('Saved request digest changed')
                request=CreateTaskRequest(model=adapter.model_id,input=params)
                save(args.output/f'request-{int(key):02d}.json',request.model_dump(mode='json',exclude_none=True))
                # The pending marker survives a process crash during submission.
                ledger[key]={'position':int(key),'status':'submission_uncertain','submitted_at':time.time(),
                    'prompt_sha256':item['request_text_sha256'],'request_path':str(args.output/f'request-{int(key):02d}.json')}
                if previous:ledger[key]['retry_of_task_id']=previous['task_id']
                save(ledger_path,ledger)
                try:
                    receipt=await client.create_task_once(request)
                    if not isinstance(receipt.task_id,str) or not receipt.task_id.strip():
                        raise ValueError('Provider did not return a task identifier')
                    ledger[key].update(task_id=receipt.task_id,status='pending',accepted_at=time.time())
                    save(ledger_path,ledger)  # Save before moving to another task or doing any polling.
                    print('accepted',key,receipt.task_id,flush=True)
                except Exception as error:
                    http_status=getattr(error,'status_code',None)
                    ledger[key].update(error_type=type(error).__name__,http_status=http_status,
                        provider_error_code=getattr(error,'error_code',None))
                    if http_status in {400,401,402,403,404,422,429}:ledger[key]['status']='submission_rejected'
                    save(ledger_path,ledger)
                    print(ledger[key]['status'],key,type(error).__name__,flush=True)
        if args.action=='poll':
            async with httpx.AsyncClient(timeout=90,follow_redirects=True) as downloads:
                for key,receipt in ledger.items():
                    if not receipt.get('task_id') or receipt.get('status')=='failed':continue
                    if (receipt.get('status')=='success' and receipt.get('image_urls')
                            and len(receipt.get('files',[]))==len(receipt['image_urls'])
                            and all(Path(path).is_file() for path in receipt['files'])):continue
                    try:
                        result=await adapter.query_task(receipt['task_id'])
                        status=result.status.value
                        receipt.update(status=status,last_checked_at=time.time(),image_urls=result.image_urls,
                            fail_code=result.fail_code,fail_msg=result.fail_msg)
                        save(ledger_path,ledger)
                        if status=='success':
                            for index,url in enumerate(result.image_urls):
                                path=args.output/f'{int(key):02d}-{index+1}.png'
                                response=await downloads.get(url);response.raise_for_status()
                                temporary=path.with_suffix('.png.tmp')
                                temporary.write_bytes(response.content);temporary.replace(path)
                                if str(path) not in receipt.setdefault('files',[]):receipt['files'].append(str(path))
                                save(ledger_path,ledger)
                            receipt['elapsed_seconds']=round(time.time()-receipt['submitted_at'],3)
                            save(ledger_path,ledger)
                        print('polled',key,receipt['task_id'],status,flush=True)
                    except Exception as error:
                        receipt.update(last_query_error_type=type(error).__name__,last_checked_at=time.time())
                        save(ledger_path,ledger)
                        print('query_error',key,type(error).__name__,flush=True)
    finally:await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['submit','poll'])
    parser.add_argument('--credentials',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--upload-direct',action='store_true',help='Local verification upload route; does not alter image requests')
    parser.add_argument('--retry-failed-from',type=Path,help='Explicitly retry only provider-confirmed failed tasks; keeps original receipts')
    args=parser.parse_args()
    if args.action=='submit' and (not args.plan or not args.manifest):parser.error('submit requires plan and manifest')
    os.environ.setdefault('DATABASE_URL','postgresql://unused@127.0.0.1:1/isolated')
    os.environ.setdefault('JWT_SECRET_KEY','local-ecommerce-verification-only')
    credentials(args.credentials)
    asyncio.run(run(args))


if __name__=='__main__':main()
