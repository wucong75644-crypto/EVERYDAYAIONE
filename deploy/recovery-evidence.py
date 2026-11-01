"""Bind a pre-mutation recovery to a private local release log and stable main.

Missing/ambiguous evidence retains the original failed-candidate recovery contract.
This helper never writes production and cannot declare a deployment successful.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def evidence(repo, owner, failed, previous):
    base=git(repo,'merge-base','origin/main',failed).decode().strip()
    paths=git(repo,'diff','--name-only','--diff-filter=AM',base,failed,'--','backend/','deploy/').decode().splitlines()
    candidate={p:hashlib.sha256(git(repo,'show',failed+':'+p)).hexdigest() for p in paths}
    match=re.fullmatch(r'\d{14}-(\d+)-\d+-\d+',owner)
    if not match:
        return candidate
    common=Path(git(repo,'rev-parse','--path-format=absolute','--git-common-dir').decode().strip())
    logs=list((common/'release-logs').glob('*-'+failed+'-'+match[1]+'.log'))
    if len(logs)!=1:
        return candidate
    log=logs[0]
    if log.is_symlink() or log.stat().st_uid!=os.getuid() or log.stat().st_mode & 0o077:
        raise RuntimeError('Private release log ownership or permissions invalid')
    raw=log.read_bytes()
    stages=re.findall(rb'^DEPLOY_STAGE stage=([^\r\n ]+)',raw,re.M)
    if stages not in ([b'preflight'],[b'preflight',b'tests_and_build']):
        return candidate  # A remote mutation or unknown stage requires the old path.
    try:
        os.kill(int(match[1]),0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError('Original local publisher may still be running')
    ancestors={os.getpid()}
    current=os.getppid()
    # Exclude the current recover command, but never another publisher.
    rows=subprocess.check_output(['ps','-axo','pid=,ppid=,args='],text=True).splitlines()
    processes={int(v[0]):(int(v[1]),v[2]) for row in rows if len(v:=row.strip().split(None,2))==3}
    while current in processes and current not in ancestors:
        ancestors.add(current);current=processes[current][0]
    if any(pid not in ancestors and ('bash deploy/deploy.sh' in command or 'bash deploy/release.sh' in command)
           for pid,(_,command) in processes.items()):
        raise RuntimeError('Another local publisher may still be running')
    if not re.fullmatch('[a-f0-9]{40}',previous or ''):
        raise RuntimeError('Pre-mutation recovery requires the unchanged production commit')
    for descendant in ('origin/main',failed):
        subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',previous,descendant],check=True)
    baseline={}
    for name in paths:
        exists=subprocess.run(['git','-C',str(repo),'cat-file','-e',previous+':'+name],capture_output=True).returncode==0
        baseline[name]=hashlib.sha256(git(repo,'show',previous+':'+name)).hexdigest() if exists else None
    return dict(recovery_mode='pre_mutation',previous_commit=previous,files=baseline,
                local_log=dict(name=log.name,sha256=hashlib.sha256(raw).hexdigest(),
                               stages=[v.decode() for v in stages]))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('repo','owner','failed','previous'):parser.add_argument(name)
    args=parser.parse_args()
    result=evidence(Path(args.repo),args.owner,args.failed,args.previous)
    print(base64.b64encode(json.dumps(result).encode()).decode())
