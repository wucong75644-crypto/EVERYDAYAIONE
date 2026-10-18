#!/usr/bin/env python3
"""Codex task instruction -> final main integration, exact checks, queued delivery.

The agent must obtain the human instruction in its chat. Literal CLI arguments
prevent accidental mode reuse; they do not cryptographically attest a chat.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import time

import delivery

ROOT = delivery.ROOT
REPOSITORY = os.environ.get('GITHUB_REPOSITORY', 'wucong75644-crypto/EVERYDAYAIONE')


def clean_task():
    branch = delivery.git('branch', '--show-current')
    if not re.fullmatch(r'codex/task/[A-Za-z0-9_.-]+', branch):
        raise RuntimeError('Use the original task branch')
    primary = delivery.git('worktree', 'list', '--porcelain').splitlines()[0].removeprefix('worktree ')
    if ROOT.resolve() == Path(primary).resolve():
        raise RuntimeError('Never mutate or close the primary checkout')
    if delivery.git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Commit scoped task edits or resolve the preserved conflict before continuing')
    return branch, delivery.git('rev-parse', 'HEAD')


def prepare():
    branch, before = clean_task()
    subprocess.run(['git','fetch','origin','refs/heads/main:refs/remotes/origin/main',
                    'refs/heads/'+branch+':refs/remotes/origin/'+branch], cwd=ROOT, check=True)
    if subprocess.run(['git','merge-base','--is-ancestor','origin/'+branch,before],cwd=ROOT).returncode:
        raise RuntimeError('Remote task diverged; preserve both versions and integrate deliberately')
    merged = subprocess.run(['git','merge','--no-edit','origin/main'],cwd=ROOT)
    if merged.returncode:
        raise RuntimeError('MAIN_INTEGRATION_CONFLICT: preserve merge state; Codex must resolve using both changes, test, commit, then rerun')
    clean_task()
    sha = delivery.git('rev-parse','HEAD')
    # Controlled merge push: fast-forward only, exact current task ref, no force.
    subprocess.run(['git','push','origin',sha+':refs/heads/'+branch],cwd=ROOT,check=True)
    delivery.verify_source({'sha':sha,'branch':branch})
    print('TASK_PREPARED sha='+sha,flush=True)
    return branch, sha


def runs(sha):
    return delivery.api(REPOSITORY,'actions/runs?head_sha='+sha+'&per_page=100')['workflow_runs']


def wait_run(run_id, sha, timeout=1800):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        run=delivery.api(REPOSITORY,'actions/runs/'+str(run_id))
        if run['head_sha']!=sha:
            raise RuntimeError('Run no longer identifies the candidate')
        state=(run['status'],run.get('conclusion'))
        if state!=last:
            print('TASK_RUN id='+str(run_id)+' status='+str(state)+' url='+run['html_url'],flush=True);last=state
        if run['status']=='completed':
            if run['conclusion']!='success':
                raise RuntimeError('Run failed/cancelled; preserve the task, do not deploy an older candidate')
            return run
        time.sleep(20)
    raise RuntimeError('Wait timed out; preserve the task and inspect the existing run, do not blindly duplicate it')


def ensure_checks(sha):
    candidates=[r for r in runs(sha) if r['path']=='.github/workflows/pre-release-checks.yml']
    if not candidates:
        subprocess.run(['python3','scripts/ci/request-check.py'],cwd=ROOT,check=True)
        for _ in range(15):
            candidates=[r for r in runs(sha) if r['path']=='.github/workflows/pre-release-checks.yml']
            if candidates:break
            time.sleep(2)
    if not candidates:
        raise RuntimeError('Requested check not visible yet; inspect before retry')
    run=max(candidates,key=lambda r:r['id'])
    wait_run(run['id'],sha)
    delivery.verify_checks(REPOSITORY,run['id'],sha)
    return run['id']


def finalize_acceptance(run_id, authorization=None):
    if authorization != "清理工作树":
        raise RuntimeError("Original task closure requires the explicit acceptance instruction")
    branch,sha=clean_task()
    run=delivery.api(REPOSITORY,'actions/runs/'+str(run_id))
    plan=delivery.parse_request('refs/tags/'+run['head_branch'],run['head_sha'])
    if (run['status']!='completed' or run['conclusion']!='success' or run['event']!='push'
            or run['path']!='.github/workflows/delivery.yml' or plan['mode']!='accept'
            or run.get('actor',{}).get('login')!=REPOSITORY.split('/')[0]
            or plan['sha']!=sha or plan['branch']!=branch):
        raise RuntimeError('No successful exact acceptance; original task must remain')
    jobs=delivery.api(REPOSITORY,'actions/runs/'+str(run_id)+'/jobs?filter=latest&per_page=100')['jobs']
    accepted=[j for j in jobs if j['name']=='acceptance' and j['conclusion']=='success']
    if len(accepted)!=1:
        raise RuntimeError('Acceptance job did not complete successfully')
    delivery.verify_checks(REPOSITORY,plan['check_run'],sha)
    delivery.verify_source({'sha':sha,'branch':branch},require_main=False)
    subprocess.run(['git','fetch','origin','refs/heads/main:refs/remotes/origin/main'],cwd=ROOT,check=True)
    if subprocess.run(['git','merge-base','--is-ancestor',sha,'origin/main'],cwd=ROOT).returncode:
        raise RuntimeError('Accepted candidate is not contained in main')
    if delivery.git('rev-parse','origin/main^{tree}')!=delivery.git('rev-parse',sha+'^{tree}'):
        raise RuntimeError('Main changed after acceptance; preserve original task for review')
    if clean_task()!=(branch,sha):
        raise RuntimeError('Task changed during acceptance; preserve it')
    subprocess.run(['bash','scripts/task-worktree.sh','sync-stable-base','--commit',
                    delivery.git('rev-parse','origin/main'),'--exclude-path',str(ROOT)],cwd=ROOT,check=True)
    subprocess.run(['bash','scripts/task-worktree.sh','close','--current','--confirm'],cwd=ROOT,check=True)


def submit(mode, authorization, *, check_run=None, message=None, files=()):
    expected={'production':'提交部署','accept':'清理工作树'}
    if mode in expected and authorization!=expected[mode]:
        raise RuntimeError('This command requires the human instruction for this exact task')
    if mode=='accept' and (message or files):
        raise RuntimeError('Acceptance never commits changes or integrates a different candidate')
    if files:
        if not message:raise RuntimeError('Scoped commit message required')
        subprocess.run(['bash','git-push.sh','--message',message,
                        *[arg for file in files for arg in ('--file',file)]],cwd=ROOT,check=True)
    branch,sha=clean_task() if mode=='accept' else prepare()
    if check_run is None:check_run=ensure_checks(sha)
    tag=delivery.request(str(check_run),mode,False,authorization)
    for _ in range(30):
        matched=[r for r in runs(sha) if r['path']=='.github/workflows/delivery.yml' and r['head_branch']==tag]
        if matched:
            run=wait_run(matched[0]['id'],sha,timeout=7200)
            if mode=='accept':finalize_acceptance(run['id'],authorization)
            return run
        time.sleep(2)
    raise RuntimeError('Delivery requested but not visible yet; inspect the tag before repeating')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('prepare')
    final=sub.add_parser('finalize');final.add_argument('--run',required=True);final.add_argument('--authorization',required=True,choices=('清理工作树',))
    for name in ('deploy','accept','rehearsal'):
        cmd=sub.add_parser(name)
        cmd.add_argument('--authorization',choices=('提交部署','清理工作树'))
        cmd.add_argument('--check-run')
        cmd.add_argument('--message')
        cmd.add_argument('--file',action='append',default=[])
    args=parser.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='finalize':finalize_acceptance(args.run,args.authorization)
    else:submit({'deploy':'production','accept':'accept','rehearsal':'rehearsal'}[args.action],args.authorization,
                check_run=args.check_run,message=args.message,files=args.file)

if __name__=='__main__':main()
