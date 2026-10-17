"""Native server proxy incidents: durable error log and per-admin WeCom delivery."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

ROOT = Path('/var/lib/everydayai-us-proxy')
STATUS = ROOT/'status.json'


def classify(status, now):
    try:
        checked = datetime.fromisoformat(status['checked_at'])
        if checked.tzinfo is None or (now-checked).total_seconds()>180:
            return 'monitor_stale', True
    except (KeyError, TypeError, ValueError):
        return 'monitor_error', True
    if status.get('alert') in ('all_failed', 'monitor_error'):
        return status['alert'], True
    total,healthy=status.get('total'),status.get('healthy')
    if not isinstance(total,int) or not isinstance(healthy,int) or not 0<=healthy<=total or total<1:
        return 'monitor_error', True
    if healthy<total:
        return 'nodes_unavailable', False
    return None, False


def summary(event, *, test=False):
    state = '已恢复' if event.get('resolved_at') else '故障'
    reason = {'all_failed':'美国节点全部不可用', 'monitor_error':'节点监控执行异常',
        'monitor_stale':'节点监控状态超过3分钟未更新', 'nodes_unavailable':'部分美国节点不可用',
        'test':'企微告警链路测试'}.get(event['kind'], '节点异常')
    prefix = '【测试】' if test else '【代理告警】'
    return (f"{prefix}{reason}：{state}\n"
        f"可用节点：{event.get('healthy',0)}/{event.get('total',0)}\n"
        f"开始：{event['started_at']}\n"
        + (f"恢复：{event['resolved_at']}\n" if event.get('resolved_at') else '')
        + ('节点已自动恢复，无需更换订阅。' if event.get('resolved_at')
           else '全部节点不可用时，请提供新的美国节点订阅链接；系统将继续检测。')
        + f"\n事件：{event['id'][:12]}")


def advance(root, status, now, save):
    root=Path(root); queue=root/'alert-events'; queue.mkdir(mode=0o700,parents=True,exist_ok=True)
    state_path=root/'alert-state.json'
    try: state=json.loads(state_path.read_text())
    except (OSError, ValueError):state={}
    kind,critical=classify(status,now)
    stamp=now.isoformat(); active=state.get('active')
    path=queue/(active+'.pending.json') if active else None
    event=None
    if path and path.exists():event=json.loads(path.read_text())
    if event and event.get('resolved_at'):
        state['active']=None;event=None
    if event and event.get('critical') and not critical:
        event.update(resolved_at=stamp,last_seen_at=stamp,healthy=status.get('healthy',0),total=status.get('total',0))
        save(path,event);state['active']=None;event=None
    if kind:
        if not event:
            event={'id':state.get('active') or uuid4().hex,'started_at':stamp,'count':0,'notified_failure':False,'notified_recovery':False}
            path=queue/(event['id']+'.pending.json');state['active']=event['id']
            save(state_path,state)
        # Never suppress a critical notification just because partial failure was logged earlier.
        event.update(kind=kind, critical=event.get('critical',False) or critical,
            total=status.get('total',0),healthy=status.get('healthy',0),last_seen_at=stamp)
        sample=status.get('checked_at') or stamp
        if sample != event.get('sample'):
            event['sample']=sample;event['count']+=1
        save(path,event)
    elif event:
        event['resolved_at']=stamp;event['last_seen_at']=stamp
        event['healthy']=status.get('healthy',0);event['total']=status.get('total',0)
        save(path,event);state['active']=None
    save(state_path,state)
    return sorted(queue.glob('*.pending.json'))[:5]


def write_error(db, event, *, test=False):
    # Stable primary key handles replay even after the incident is resolved.
    with db.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute('''INSERT INTO public.error_logs
            (id,fingerprint,level,module,function,line,message,occurrence_count,
             first_seen_at,last_seen_at,org_id,is_critical,is_resolved,resolved_at)
            VALUES(%s,%s,%s,'us_proxy','health_monitor',0,%s,%s,%s,%s,NULL,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET level=EXCLUDED.level,message=EXCLUDED.message,
              occurrence_count=GREATEST(error_logs.occurrence_count,EXCLUDED.occurrence_count),
              last_seen_at=EXCLUDED.last_seen_at,is_critical=EXCLUDED.is_critical,
              is_resolved=EXCLUDED.is_resolved,resolved_at=EXCLUDED.resolved_at''',
            (event['log_id'],event['id'],'INFO' if test else 'CRITICAL' if event['critical'] else 'WARNING',
             summary(event,test=test),max(1,event['count']),event['started_at'],event['last_seen_at'],
             False if test else event['critical'],bool(event.get('resolved_at')),event.get('resolved_at')))


def target(db):
    from services.org.config_resolver import OrgConfigResolver
    resolver=OrgConfigResolver(db)
    with db.pool.connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT DISTINCT u.id,w.org_id,w.wecom_userid FROM users u
            JOIN org_members m ON m.user_id=u.id AND m.status='active' AND m.role IN ('owner','admin')
            JOIN wecom_user_mappings w ON w.user_id=u.id AND w.org_id=m.org_id
            WHERE u.role='super_admin' AND u.status='active' ORDER BY u.id,w.org_id''')
        rows=cur.fetchall()
    if len({str(row['id']) for row in rows})!=1:raise RuntimeError('Unique platform-admin recipient required')
    for row in rows:
        org=str(row['org_id'])
        corp=resolver.get(org,'wecom_corp_id');agent=resolver.get(org,'wecom_agent_id');secret=resolver.get(org,'wecom_agent_secret')
        if corp and agent and secret:
            from services.wecom.app_message_sender import OrgWecomCreds
            return row['wecom_userid'],OrgWecomCreds(org,corp,int(agent),secret)
    raise RuntimeError('Admin WeCom app configuration unavailable')


async def deliver_events(db, paths, save, sender, recipient, *, test=False):
    complete=0
    for path in paths:
        event=json.loads(path.read_text())
        if not event.get('log_id'):
            with db.pool.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT nextval(pg_get_serial_sequence('public.error_logs','id')) AS id")
                event['log_id']=cur.fetchone()['id']
            save(path,event)
        write_error(db,event,test=test)
        if event['critical']:
            key='notified_recovery' if event.get('resolved_at') else 'notified_failure'
            if not event.get(key):
                user,creds=recipient(db)
                if not await sender(user,summary(event,test=test),creds):
                    raise RuntimeError('WeCom API did not acknowledge delivery')
                event[key]=True
                save(path,event)
        if event.get('resolved_at'):
            path.rename(path.with_name(event['id']+'.done.json'))
        complete+=1
    return complete


def safe_logs(record):
    record['message']=re.sub(r'(?i)(access_token|corpsecret|secret|password|token)=([^&\s]+)',r'\1=[redacted]',record['message'])
    return True


async def run(args, manager):
    now=datetime.now(timezone.utc)
    try: status=json.loads(Path(args.status).read_text())
    except (OSError,ValueError):status={}
    if not isinstance(status,dict):status={}
    paths=advance(args.root,status,now,manager.atomic_json)
    if args.test:
        stamp=now.isoformat();event={'id':uuid4().hex,'kind':'test','critical':True,'count':1,
            'started_at':stamp,'last_seen_at':stamp,'resolved_at':stamp,
            'healthy':status.get('healthy',0),'total':status.get('total',0)}
        path=Path(args.root)/'alert-events'/(event['id']+'.pending.json')
        manager.atomic_json(path,event);paths=[path]
    if not paths:
        print('US_PROXY_ALERT healthy_no_pending_events');return
    from core.database import get_db
    from services.wecom.app_message_sender import send_text
    db=get_db()
    try:
        count=await deliver_events(db,paths,manager.atomic_json,send_text,target,test=args.test)
        print('US_PROXY_ALERT processed='+str(count)+' test='+str(args.test))
    finally:db.pool.close()


def main():
    import asyncio
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default=str(ROOT));parser.add_argument('--status',default=str(STATUS))
    parser.add_argument('--test',action='store_true');args=parser.parse_args()
    sys.path.insert(0,'/var/www/everydayai/backend')
    os.chdir('/var/www/everydayai/backend')
    from dotenv import load_dotenv
    load_dotenv('/var/www/everydayai/backend/.env',override=False)
    from loguru import logger
    logger.remove();logger.add(sys.stderr,filter=safe_logs,level='WARNING')
    spec=importlib.util.spec_from_file_location('manager',Path(__file__).with_name('us_failover.py'))
    manager=importlib.util.module_from_spec(spec);spec.loader.exec_module(manager)
    try:
        asyncio.run(run(args,manager))
    except Exception as error:
        print('US_PROXY_ALERT_PENDING '+type(error).__name__)
        raise SystemExit(1)


if __name__=='__main__':main()
