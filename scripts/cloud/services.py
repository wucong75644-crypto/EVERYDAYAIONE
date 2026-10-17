#!/usr/bin/env python3
"""Private, task-isolated loopback services. Never applies application migrations."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import tempfile

BASE = Path('/workspace/.everydayaione-dev')
RUNTIME = BASE / 'runtime'
PG = RUNTIME / 'usr/lib/postgresql/17/bin'
os.environ['LD_LIBRARY_PATH'] = str(RUNTIME / 'usr/lib/x86_64-linux-gnu')
root = Path(sys.argv[2]).resolve()
assert root.is_relative_to('/workspace') and (root / 'scripts/cloud/dev.py').is_file()
state = BASE / 'instances' / hashlib.sha256(str(root).encode()).hexdigest()[:16]
state.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(state, 0o700)
meta = state / 'local.json'

def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)

def private_write(path, text):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        os.chmod(f.name,0o600);f.write(text);f.flush();os.fsync(f.fileno())
        temporary=f.name
    os.replace(temporary,path)

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

if not meta.exists():
    assert not (state / 'pgdata').exists(), 'Unrecognized data: preserve and inspect'
    m = dict(root=str(root), pgport=port(), redisport=port(), password=secrets.token_hex(32))
    m['redis_password'] = secrets.token_hex(32)
    with meta.open('x') as f:
        os.chmod(meta, 0o600)
        json.dump(m, f)
else:
    m = json.loads(meta.read_text())
assert m['root'] == str(root)
dsn = dict(host='127.0.0.1', port=m['pgport'], user='dev_admin', password=m['password'], dbname='postgres')
import psycopg
import redis

def redis_client():
    return redis.Redis(host='127.0.0.1', port=m['redisport'], password=m['redis_password'], db=15)

def start():
    if not (state / 'pgdata/PG_VERSION').exists():
        pw = state / 'init-password'
        pw.write_text(m['password']); os.chmod(pw, 0o600)
        try:
            with (state / 'init.log').open('w') as log:
                run([PG/'initdb', '-D', state/'pgdata', '-U', 'dev_admin', '--pwfile', pw,
                     '--auth-local=scram-sha-256', '--auth-host=scram-sha-256', '--locale=C.UTF-8'], stdout=log, stderr=log)
        finally:
            pw.unlink(missing_ok=True)
        (state/'pgdata/postgresql.conf').open('a').write(
            f"\nlisten_addresses='127.0.0.1'\nport={m['pgport']}\nunix_socket_directories='{state}'\n")
    status = subprocess.run([str(PG/'pg_ctl'), '-D', str(state/'pgdata'), 'status'], capture_output=True)
    if status.returncode:
        run([PG/'pg_ctl', '-D', state/'pgdata', '-l', state/'postgres.log', '-w', 'start'], stdout=subprocess.DEVNULL)
    with psycopg.connect(**dsn) as c:
        assert c.execute('SHOW listen_addresses').fetchone()[0] == '127.0.0.1'
        assert Path(c.execute('SHOW data_directory').fetchone()[0]).resolve()==(state/'pgdata').resolve()
    try:
        assert redis_client().ping()
    except redis.ConnectionError:
        cfg = state/'redis.conf'
        cfg.write_text(f'''bind 127.0.0.1
protected-mode yes
port {m['redisport']}
dir {state}
pidfile {state}/redis.pid
logfile {state}/redis.log
requirepass {m['redis_password']}
appendonly yes
appendfsync always
daemonize yes
''')
        os.chmod(cfg, 0o600)
        run([RUNTIME/'usr/bin/redis-server', cfg])
        for _ in range(50):
            try:
                if redis_client().ping(): break
            except redis.ConnectionError: time.sleep(.1)
        else: raise RuntimeError('Redis not ready')
    redis_dir=redis_client().config_get('dir')['dir']
    if isinstance(redis_dir,bytes):redis_dir=redis_dir.decode()
    assert Path(redis_dir).resolve()==state.resolve()
    print('Task-private PostgreSQL and Redis ready; loopback only, Redis DB 15')

command = sys.argv[1]
if command == 'start':
    start()
elif command == 'prepare':
    start()
    sys.path.insert(0,str(root/'scripts/cloud/bootstrap'))
    from apply import prepare,verify
    print('Development bootstrap:',prepare(root))
    # Only these private loopback instances are configured. Never replace an
    # existing user-supplied database binding, or copy another checkout's .env.
    from dotenv import dotenv_values
    envfile=root/'backend/.env'
    values=dict(dotenv_values(envfile))
    password=m.get('app_password') or secrets.token_hex(32)
    url=f'postgresql://everydayai:{password}@127.0.0.1:{m["pgport"]}/cloud_dev_app_v1'
    prior_url=f'postgresql://everydayai:{password}@127.0.0.1:{m["pgport"]}/{m.get("app_database","cloud_dev_app")}'
    existing=values.get('DATABASE_URL','')
    if 'change-me' not in existing and existing not in (url,prior_url):
        raise RuntimeError('Preserved existing database configuration; refusing to replace it')
    with psycopg.connect(**dsn,autocommit=True) as c:
        from psycopg import sql
        c.execute(sql.SQL('ALTER ROLE everydayai LOGIN PASSWORD {}').format(sql.Literal(password)))
    m['app_password']=password
    m['app_database']='cloud_dev_app_v1'
    private_write(meta,json.dumps(m))
    with psycopg.connect(url) as c: verify(c)
    values.update(DATABASE_URL=url,REDIS_HOST='127.0.0.1',REDIS_PORT=str(m['redisport']),
        REDIS_PASSWORD=m['redis_password'],REDIS_DB='15',REDIS_SSL='false',
        KB_ENABLED='false',MEMORY_ENABLED_DEFAULT='false',SANDBOX_ENABLED='false',
        CONVERSATION_ACTOR_WEB_ENABLED='false',CONVERSATION_ACTOR_WORKER_ENABLED='false',
        WECOM_BOT_ENABLED='false',AGENT_REACH_ENABLED='false',SOCIAL_OPS_ENABLED='false',
        SOCIAL_OPS_SCHEDULED_PUBLISH_ENABLED='false',SENTRY_DSN='',
        FILE_WORKSPACE_ROOT=str(root/'.cloud-dev/workspace'))
    values['CLOUD_LOG_DIR']=str(root/'.cloud-dev/application-logs')
    private_write(envfile,''.join(f'{key}={json.dumps(value)}\n' for key,value in values.items() if value is not None))
    print('Private development connections configured; no external credentials')
elif command == 'stop':
    redis_client().shutdown(save=True)
    run([PG/'pg_ctl', '-D', state/'pgdata', '-m', 'fast', '-w', 'stop'], stdout=subprocess.DEVNULL)
    print('Stopped only this task private services')
elif command == 'seed-markers':
    with psycopg.connect(**dsn) as c:
        c.execute('CREATE TABLE IF NOT EXISTS public.cloud_dev_persistence (id int PRIMARY KEY, value text)')
        c.execute("INSERT INTO public.cloud_dev_persistence VALUES (1, 'synthetic-only') ON CONFLICT DO NOTHING")
        assert c.execute('SELECT value FROM public.cloud_dev_persistence WHERE id=1').fetchone()[0]=='synthetic-only'
    r=redis_client(); r.setnx('cloud-dev:persistence', 'synthetic-only')
    assert r.get('cloud-dev:persistence') == b'synthetic-only'
    print('Synthetic persistence markers seeded')
elif command == 'verify':
    with psycopg.connect(**dsn) as c:
        assert c.execute('SELECT value FROM public.cloud_dev_persistence WHERE id=1').fetchone()[0]=='synthetic-only'
    assert redis_client().get('cloud-dev:persistence') == b'synthetic-only'
    print('Existing PostgreSQL and Redis persistence markers verified without writing')
else:
    raise SystemExit('Use start, stop, verify')
