#!/usr/bin/env python3
"""Verify dedicated SSH credentials without app configuration or data access."""
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import tempfile
from build_ssh_policy import build
from delivery import config_text, verify_environment, verify_actor, write_ssh_file


def safe_probe_report(raw):
    """Accept only the fixed gateway protocol; never publish arbitrary SSH output."""
    try:
        value = json.loads(raw)
        report = {}
        for key in ('protocol', 'uid'):
            if type(value[key]) is not int:
                raise ValueError()
            report[key] = value[key]
        if report['protocol'] != 1 or report['uid'] != 0:
            raise ValueError()
        for key, names in {
            'paths_exist': [ '/var/www/everydayai' + suffix for suffix in
                            ('', '/frontend', '/backend', '/deploy', '/backend/venv/bin/python',
                             '/.production-database-identity.json')],
            'services_active': ['everydayai-backend', 'everydayai-sync', 'everydayai-wecom',
                                'everydayai-conversation-actor', 'nginx'],
        }.items():
            if set(value[key]) != set(names) or any(type(value[key][name]) is not bool for name in names):
                raise ValueError()
            report[key] = {name: value[key][name] for name in names}
        for key in ('lock_present', 'database_accessed', 'deployed'):
            if type(value[key]) is not bool:
                raise ValueError()
            report[key] = value[key]
        for key, pattern in {'production_commit': r'(?:[a-f0-9]{40})?',
                             'policy_source': r'[a-f0-9]{40}',
                             'policy_sha256': r'[a-f0-9]{64}',
                             'gateway_sha256': r'[a-f0-9]{64}',
                             'policy_scripts_sha256': r'[a-f0-9]{64}'}.items():
            if not isinstance(value[key], str) or not re.fullmatch(pattern, value[key]):
                raise ValueError()
            report[key] = value[key]
        return report
    except (ValueError, TypeError, KeyError):
        raise RuntimeError('Invalid dedicated SSH probe report; raw output withheld') from None


def verify_connection():
    verify_actor(os.environ['GITHUB_REPOSITORY'],os.environ['DELIVERY_APPROVER'])
    verify_environment(os.environ['GITHUB_REPOSITORY'],'production',os.environ['DELIVERY_APPROVER'])
    metadata={}
    for line in config_text(os.environ['DELIVERY_CONFIG_JSON']).splitlines():
        key,value=line.split('=',1);metadata[key]=shlex.split(value)[0]
    with tempfile.TemporaryDirectory(prefix='delivery-probe-') as directory:
        root=Path(directory)
        for filename,value in [('key',os.environ['DELIVERY_SSH_KEY']),('hosts',os.environ['DELIVERY_KNOWN_HOSTS'])]:
            write_ssh_file(root/filename, value)
        command=['ssh','-F','/dev/null','-i',str(root/'key'),'-p',metadata['SERVER_PORT'],
                 '-o','UserKnownHostsFile='+str(root/'hosts'),'-o','StrictHostKeyChecking=yes',
                 '-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','BatchMode=yes',
                 '-o','ConnectTimeout=15','-o','ClearAllForwardings=yes','-T',
                 metadata['SERVER_USER']+'@'+metadata['SERVER_HOST']]
        clean={k:v for k,v in os.environ.items() if not k.startswith('DELIVERY_')}
        result=subprocess.run(command+['everydayai-ci-probe-v1'],env=clean,capture_output=True,text=True,timeout=30)
        if result.returncode not in (0, 1) or not result.stdout.strip().startswith('{'):
            reason=next((name for name,pattern in [('key-format','error in libcrypto|invalid format'),('authentication','Permission denied'),('host-identity','Host key verification failed|REMOTE HOST IDENTIFICATION'),('network','Connection refused|Connection timed out|No route'),('gateway','SSH_GATEWAY_DENIED')] if re.search(pattern,result.stderr)), 'remote-probe-or-ssh')
            raise RuntimeError(f'Dedicated SSH probe failed: category={reason}, exit={result.returncode}; no deployment attempted')
        report=safe_probe_report(result.stdout)
        expected=hashlib.sha256((Path(__file__).parent/'ssh_gateway.py').read_bytes()).hexdigest()
        expected_scripts=hashlib.sha256(json.dumps(build()['scripts'],sort_keys=True).encode()).hexdigest()
        if (report['gateway_sha256']!=expected or report['policy_scripts_sha256']!=expected_scripts
                or report['database_accessed'] or report['deployed']):
            raise RuntimeError('Gateway identity mismatch')
        denied=subprocess.run(command+['id'],env=clean,capture_output=True,text=True,timeout=30)
        if denied.returncode==0 or 'SSH_GATEWAY_DENIED' not in denied.stderr:
            raise RuntimeError('Arbitrary SSH command was not denied')
        source=report['policy_source']
        if not re.fullmatch(r'[a-f0-9]{40}',source) or subprocess.run(['git','merge-base','--is-ancestor',source,'HEAD'],capture_output=True).returncode:
            raise RuntimeError('Installed policy is from an unrelated candidate')
        report['arbitrary_command_denied']=True
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:
            stream.write('Read-only release probe completed. No database/Redis connection, application configuration read, restart or deployment.\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n')
        print(json.dumps(report))
        if (result.returncode or not all(report['paths_exist'].values())
                or not all(report['services_active'].values()) or not report['production_commit']
                or report['lock_present']):
            raise RuntimeError('Dedicated SSH probe reports an unhealthy release state; no deployment attempted')

if __name__=='__main__': verify_connection()
