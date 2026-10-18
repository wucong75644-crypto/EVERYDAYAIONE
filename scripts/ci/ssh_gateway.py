#!/usr/bin/python3.11
"""Root-owned forced SSH command for the dedicated GitHub release key.

This narrows SSH operations; approved application deployments retain the existing
executor's root authority. Never install this beneath the writable app directory.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

POLICY = Path('/etc/everydayai-ci/policy.json')
APP = '/var/www/everydayai'
PYTHON = APP + '/backend/venv/bin/python'


def classify(command):
    args = shlex.split(command)
    if args == ['everydayai-ci-probe-v1']:
        return 'probe', args
    if args == ['echo', 'SSH连接成功']:
        return 'echo', args
    if args[:2] == ['rsync', '--server']:
        # Receiver only. No sender, alternate paths, protect-args or arbitrary
        # options. These are the flags emitted by rsync -avz [--delete].
        tail = args[2:]
        if len(tail) not in (3, 4) or tail[-2] != '.':
            raise ValueError('Invalid receiver shape')
        options = tail[:-2]
        short = [x for x in options if re.fullmatch(r'-[vlogDtprze.iLsfxCIu]+', x)]
        if len(short) != 1 or any(x != '--delete' and x not in short for x in options):
            raise ValueError('Invalid receiver options')
        raw = tail[-1]
        if raw not in (APP+'/frontend/', APP+'/backend/', APP+'/backend/../deploy/'):
            raise ValueError('Receiver path denied')
        target = os.path.normpath(raw)
        if Path(target).is_symlink() or Path(target).resolve() != Path(target):
            raise ValueError('Receiver symlink denied')
        return 'rsync', ['/usr/bin/rsync', '--server', *options,
                          '--filter=P .env*', '--filter=P config.env', '--filter=P venv/***', '.', raw]
    if args[:3] == ['bash', '-s', '--'] and len(args) >= 6 and args[3] == APP:
        action, token = args[4:6]
        if not re.fullmatch(r'[A-Za-z0-9-]+', token):
            raise ValueError('Invalid lock token')
        if action == 'record':
            if len(args) != 9 or not re.fullmatch(r'[a-f0-9]{40}', args[6]) or args[7] not in ('preview','main','rollback') or not re.fullmatch(r'[A-Za-z0-9_./-]+',args[8]):
                raise ValueError('Invalid provenance request')
        elif action not in ('acquire','assert','release','invalidate','read','read-source') or len(args) != 6:
            raise ValueError('Invalid coordination request')
        return 'coordination', args
    if args[:3] == ['bash', '-s', '--'] and len(args) > 3:
        if not all(re.fullmatch(r'backend/migrations/[0-9]{3}_[A-Za-z0-9_]+\.sql', x) for x in args[3:]):
            raise ValueError('Invalid migration path')
        return 'migrations', args
    if args == ['bash']:
        return 'deployment', args
    if args == ['sudo','bash','-s']:
        return 'storage', args
    if args[:2] == [PYTHON, '-'] and (len(args)==2 or len(args)==3 and re.fullmatch(r'[A-Za-z0-9-]+',args[2])):
        return 'python', args
    raise ValueError('Command denied')


def probe(policy):
    paths = [APP, APP+'/frontend', APP+'/backend', APP+'/deploy', APP+'/backend/venv/bin/python',
             APP+'/.production-database-identity.json']
    checks = {p: Path(p).exists() for p in paths}
    marker = Path(APP+'/.release-provenance')
    commit = ''
    if marker.is_file():
        for line in marker.read_text().splitlines():
            if line.startswith('commit=') and re.fullmatch(r'[0-9a-f]{40}', line[7:]):
                commit = line[7:]
    services = {}
    for unit in ('everydayai-backend','everydayai-sync','everydayai-wecom','everydayai-conversation-actor','nginx'):
        services[unit] = subprocess.run(['/usr/bin/systemctl','is-active','--quiet',unit],capture_output=True).returncode == 0
    result = {'protocol':1,'uid':os.getuid(),'paths_exist':checks,'services_active':services,
              'production_commit':commit,'lock_present':Path(APP+'.release-lock').exists(),
              'policy_sha256':hashlib.sha256(POLICY.read_bytes()).hexdigest(),
              'gateway_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'policy_scripts_sha256':hashlib.sha256(json.dumps(policy['scripts'],sort_keys=True).encode()).hexdigest(),
              'policy_source':policy['source_sha'],'database_accessed':False,'deployed':False}
    print(json.dumps(result))
    return 0 if os.getuid()==0 and all(checks.values()) and all(services.values()) and commit and not result['lock_present'] else 1


def main():
    policy_stat = POLICY.stat()
    if policy_stat.st_uid != 0 or policy_stat.st_mode & 0o022:
        raise ValueError('Unsafe policy ownership')
    policy = json.loads(POLICY.read_text())
    kind, args = classify(os.environ.get('SSH_ORIGINAL_COMMAND',''))
    if kind == 'probe':
        return probe(policy)
    if kind == 'echo':
        print('SSH连接成功'); return 0
    if kind == 'rsync':
        os.execv(args[0],args)
    body = sys.stdin.buffer.read(1024*1024+1)
    if len(body)>1024*1024 or hashlib.sha256(body).hexdigest() not in policy['scripts'].get(kind,[]):
        raise ValueError('Unapproved release script; administrator must review and update policy')
    # No shell interpolation of SSH_ORIGINAL_COMMAND. Invoke fixed argv and the
    # exact reviewed stdin. Unknown scripts fail before any interpreter starts.
    return subprocess.run(args,input=body,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'}).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError):
        raise SystemExit('SSH_GATEWAY_DENIED')
