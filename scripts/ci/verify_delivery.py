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
from delivery import config_text, verify_environment


def verify_connection():
    verify_environment(os.environ['GITHUB_REPOSITORY'],'production',os.environ['DELIVERY_APPROVER'])
    metadata={}
    for line in config_text(os.environ['DELIVERY_CONFIG_JSON']).splitlines():
        key,value=line.split('=',1);metadata[key]=shlex.split(value)[0]
    with tempfile.TemporaryDirectory(prefix='delivery-probe-') as directory:
        root=Path(directory)
        for filename,value in [('key',os.environ['DELIVERY_SSH_KEY']),('hosts',os.environ['DELIVERY_KNOWN_HOSTS'])]:
            target=root/filename;target.write_text(value.rstrip()+"\n");target.chmod(0o600)
        command=['ssh','-F','/dev/null','-i',str(root/'key'),'-p',metadata['SERVER_PORT'],
                 '-o','UserKnownHostsFile='+str(root/'hosts'),'-o','StrictHostKeyChecking=yes',
                 '-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','BatchMode=yes',
                 '-o','ConnectTimeout=15','-o','ClearAllForwardings=yes','-T',
                 metadata['SERVER_USER']+'@'+metadata['SERVER_HOST']]
        clean={k:v for k,v in os.environ.items() if not k.startswith('DELIVERY_')}
        result=subprocess.run(command+['everydayai-ci-probe-v1'],env=clean,capture_output=True,text=True,timeout=30)
        if result.returncode:
            reason=next((name for name,pattern in [('key-format','error in libcrypto|invalid format'),('authentication','Permission denied'),('host-identity','Host key verification failed|REMOTE HOST IDENTIFICATION'),('network','Connection refused|Connection timed out|No route'),('gateway','SSH_GATEWAY_DENIED')] if re.search(pattern,result.stderr)), 'remote-probe-or-ssh')
            raise RuntimeError(f'Dedicated SSH probe failed: category={reason}, exit={result.returncode}; no deployment attempted')
        report=json.loads(result.stdout)
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
            stream.write('Dedicated release SSH verified. No database/Redis connection, application configuration read, restart or deployment.\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n')
        print(json.dumps(report))

if __name__=='__main__': verify_connection()
