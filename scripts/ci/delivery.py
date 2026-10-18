#!/usr/bin/env python3
"""Verify immutable checks and drive the existing controlled release entry."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SHA = r'[0-9a-f]{40}'
REQUEST = re.compile(r'refs/tags/delivery-(rehearsal|production|accept)/(' + SHA + r')/([0-9]+)/([A-Za-z0-9_.-]+)/([0-9a-f]{12})')


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def api(repository, endpoint):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise RuntimeError('Invalid repository identity')
    if shutil.which('gh'):
        result = subprocess.run(['gh', 'api', f'repos/{repository}/{endpoint}'],
                                capture_output=True, text=True, timeout=45)
        if result.returncode == 0:
            return json.loads(result.stdout)
    # Public repository read fallback for cloud tasks without gh. No credentials
    # are copied from another computer; normal platform networking still applies.
    request = Request(f'https://api.github.com/repos/{repository}/{endpoint}',
                      headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'everydayaione-delivery'})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def parse_request(ref, workflow_sha):
    match = REQUEST.fullmatch(ref)
    if not match:
        raise RuntimeError('Only explicit immutable delivery request tags are supported')
    mode, sha, run, slug, nonce = match.groups()
    if workflow_sha != sha:
        raise RuntimeError('Workflow source differs from the requested commit')
    return dict(mode=mode, sha=sha, check_run=run, branch='codex/task/' + slug)


def verify_checks(repository, run_id, sha):
    if not re.fullmatch(r'[0-9]+', str(run_id)) or not re.fullmatch(SHA, sha):
        raise RuntimeError('Invalid check identity')
    run = api(repository, f'actions/runs/{run_id}')
    if (run.get('repository', {}).get('full_name') != repository
            or run.get('head_sha') != sha or run.get('status') != 'completed'
            or run.get('conclusion') != 'success'
            or run.get('path') != '.github/workflows/pre-release-checks.yml'):
        raise RuntimeError('Required pre-release workflow has not passed for this exact SHA')
    jobs = api(repository, f'actions/runs/{run_id}/jobs?filter=latest&per_page=100')['jobs']
    for name in ('frontend', 'backend'):
        matched = [job for job in jobs if job['name'] == name]
        if len(matched) != 1 or matched[0]['status'] != 'completed' or matched[0]['conclusion'] != 'success':
            raise RuntimeError('Both frontend and backend checks must pass; skipped/cancelled is not success')
    return run['html_url']


def verify_source(plan, *, require_main=True):
    if git('rev-parse', 'HEAD') != plan['sha']:
        raise RuntimeError('Checkout does not equal the checked and approved SHA')
    tip = git('ls-remote', '--exit-code', 'origin', 'refs/heads/' + plan['branch']).split()
    if not tip or tip[0] != plan['sha']:
        raise RuntimeError('Task changed after request; recheck and request approval again')
    if require_main:
        subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=ROOT, check=True)
        if subprocess.run(['git', 'merge-base', '--is-ancestor', 'origin/main', plan['sha']], cwd=ROOT).returncode:
            raise RuntimeError('Candidate is missing latest main; integrate and recheck before approval')


def verify_environment(repository, name, approver):
    environment = api(repository, 'environments/' + name)
    rules = environment.get('protection_rules', [])
    reviewers = [item for rule in rules
                 if rule['type'] == 'required_reviewers' for item in rule['reviewers']]
    expected = len(reviewers) == 1 and reviewers[0].get('type') == 'User' and reviewers[0]['reviewer'].get('login') == approver
    if not expected or environment.get('can_admins_bypass', True):
        raise RuntimeError('Required human approval or administrator bypass protection is missing')


def make_plan():
    repository = os.environ['GITHUB_REPOSITORY']
    plan = parse_request(os.environ['GITHUB_REF'], os.environ['GITHUB_SHA'])
    if plan['mode'] != 'rehearsal' and os.environ.get('PRODUCTION_DELIVERY_ENABLED') != 'true':
        raise RuntimeError('Production delivery is disarmed; no production job may start')
    plan['checks_url'] = verify_checks(repository, plan['check_run'], plan['sha'])
    verify_source(plan, require_main=plan['mode'] != 'rehearsal')
    environment = 'delivery-rehearsal' if plan['mode'] == 'rehearsal' else 'production'
    verify_environment(repository, environment, os.environ['DELIVERY_APPROVER'])
    for key, value in plan.items():
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f'{key}={value}\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
        stream.write(f"Mode: **{plan['mode']}**\n\nCommit: `{plan['sha']}`\n\nChecks: {plan['checks_url']}\n\n"
                     'A production deployment and production acceptance are separate approvals.\n')
    print(json.dumps(plan))


def request(check_run, mode, dry_run):
    branch = git('branch', '--show-current')
    if not re.fullmatch(r'codex/task/[A-Za-z0-9_.-]+', branch):
        raise RuntimeError('Use the original task branch')
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('Commit and push the task first')
    sha = git('rev-parse', 'HEAD')
    repository = os.environ.get('GITHUB_REPOSITORY', 'wucong75644-crypto/EVERYDAYAIONE')
    plan = dict(branch=branch, sha=sha)
    verify_source(plan, require_main=mode != 'rehearsal')
    verify_checks(repository, check_run, sha)
    tag = f'delivery-{mode}/{sha}/{check_run}/{branch.removeprefix("codex/task/")}/{uuid4().hex[:12]}'
    if not dry_run:
        subprocess.run(['git', 'push', 'origin', sha + ':refs/tags/' + tag], cwd=ROOT, check=True)
    print(f'DELIVERY_REQUEST mode={mode} sha={sha} ref={tag} dry_run={dry_run}')


CONFIG_KEYS = {'SERVER_HOST', 'SERVER_USER', 'SERVER_PORT', 'REMOTE_APP_DIR',
               'REMOTE_FRONTEND_DIR', 'REMOTE_BACKEND_DIR', 'DOMAIN', 'EMAIL',
               'BACKEND_PORT', 'FRONTEND_PORT', 'RUN_MIGRATIONS'}


def config_text(raw):
    values = json.loads(raw)
    if not isinstance(values, dict) or not set(values) <= CONFIG_KEYS:
        raise RuntimeError('Only deployment connection metadata is allowed; no application/database secrets')
    required = {'SERVER_HOST', 'SERVER_USER', 'SERVER_PORT', 'REMOTE_APP_DIR',
                'REMOTE_FRONTEND_DIR', 'REMOTE_BACKEND_DIR', 'DOMAIN', 'RUN_MIGRATIONS'}
    if not required <= set(values) or any(not isinstance(v, str) or '\n' in v or '\r' in v for v in values.values()):
        raise RuntimeError('Missing or invalid deployment metadata')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', values['SERVER_HOST']):
        raise RuntimeError('Invalid server host')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', values['SERVER_USER']):
        raise RuntimeError('Invalid server user')
    if not values['SERVER_PORT'].isdigit() or not 1 <= int(values['SERVER_PORT']) <= 65535:
        raise RuntimeError('Invalid SSH port')
    for name in ('REMOTE_APP_DIR', 'REMOTE_FRONTEND_DIR', 'REMOTE_BACKEND_DIR'):
        value = values[name]
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value) or '..' in Path(value).parts:
            raise RuntimeError('Invalid deployment path')
    # The legacy executor contains fixed service paths; presenting arbitrary
    # configurable roots here would falsely suggest those paths were relocated.
    expected = {'REMOTE_APP_DIR': '/var/www/everydayai',
                'REMOTE_FRONTEND_DIR': '/var/www/everydayai/frontend',
                'REMOTE_BACKEND_DIR': '/var/www/everydayai/backend'}
    if any(values[name] != value for name, value in expected.items()):
        raise RuntimeError('Existing executor requires its fixed everydayai deployment paths')
    if values['RUN_MIGRATIONS'] not in ('true', 'false'):
        raise RuntimeError('Explicit migration setting required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', values['DOMAIN']):
        raise RuntimeError('Invalid deployment domain')
    for name in ('BACKEND_PORT', 'FRONTEND_PORT'):
        if name in values and (not values[name].isdigit() or not 1 <= int(values[name]) <= 65535):
            raise RuntimeError('Invalid application port')
    if 'EMAIL' in values and not re.fullmatch(r'[A-Za-z0-9_.+@-]+', values['EMAIL']):
        raise RuntimeError('Invalid deployment email')
    return ''.join(f'{key}={shlex.quote(value)}\n' for key, value in sorted(values.items()))


def execute():
    plan = parse_request(os.environ['GITHUB_REF'], os.environ['GITHUB_SHA'])
    if plan['mode'] not in ('production', 'accept') or os.environ.get('PRODUCTION_DELIVERY_ENABLED') != 'true':
        raise RuntimeError('Real execution is disarmed')
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Real execution requires the protected GitHub-hosted job')
    repository = os.environ['GITHUB_REPOSITORY']
    verify_environment(repository, 'production', os.environ['DELIVERY_APPROVER'])
    verify_checks(repository, plan['check_run'], plan['sha'])
    verify_source(plan)
    # Dedicated SSH and administrator-reviewed policy must still match before
    # any real release operation, including the database identity guard.
    from verify_delivery import verify_connection
    verify_connection()
    # Credentials exist only inside the protected GitHub job.
    metadata = config_text(os.environ['DELIVERY_CONFIG_JSON'])
    key = os.environ['DELIVERY_SSH_KEY']
    hosts = os.environ['DELIVERY_KNOWN_HOSTS']
    if not key.strip() or not hosts.strip():
        raise RuntimeError('Production-only credentials must be provisioned separately')
    with tempfile.TemporaryDirectory(prefix='delivery-') as directory:
        private = Path(directory)
        for name, text in [('identity', key), ('known_hosts', hosts)]:
            path = private / name
            path.write_text(text)
            path.chmod(0o600)
        ssh_config = private / 'ssh_config'
        ssh_config.write_text(f'Host *\n  IdentityFile "{private / "identity"}"\n'
                              f'  UserKnownHostsFile "{private / "known_hosts"}"\n'
                              '  StrictHostKeyChecking yes\n  IdentitiesOnly yes\n'
                              '  IdentityAgent none\n  BatchMode yes\n')
        ssh_config.chmod(0o600)
        binary = private / 'bin'
        binary.mkdir()
        wrapper = binary / 'ssh'
        wrapper.write_text('#!/bin/sh\nexec /usr/bin/ssh -F ' + shlex.quote(str(ssh_config)) + ' "$@"\n')
        wrapper.chmod(0o700)
        task = private / 'task'
        subprocess.run(['git', 'worktree', 'add', '-b', plan['branch'], str(task), plan['sha']], cwd=ROOT, check=True)
        config = task / 'deploy/config.env'
        config.write_text(metadata)
        config.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith('DELIVERY_')}
        env['PATH'] = str(binary) + os.pathsep + os.environ['PATH']
        env['EVERYDAYAI_PYTHON_BIN'] = 'python3.12'
        if plan['mode'] == 'accept':
            # Only the acceptance job has contents:write; no production deploy
            # happens here. The existing entry verifies the deployed tree first.
            auth = base64.b64encode(('x-access-token:' + os.environ['GH_TOKEN']).encode()).decode()
            env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                       GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + auth)
            git('config', 'user.name', 'github-actions[bot]')
            git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
            command = ['bash', 'deploy/release.sh', '--accept-and-close']
        else:
            command = ['bash', 'deploy/release.sh', '--deploy-task', plan['sha'],
                       '--reviewed-sha', plan['sha'], '--skip-test']
        try:
            subprocess.run(command, cwd=task, env=env, check=True)
        finally:
            if task.exists():
                subprocess.run(['git', 'worktree', 'remove', str(task)], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('plan')
    commands.add_parser('execute')
    requester = commands.add_parser('request')
    requester.add_argument('--check-run', required=True)
    requester.add_argument('--mode', choices=('rehearsal', 'production', 'accept'), default='rehearsal')
    requester.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.command == 'plan':
        make_plan()
    elif args.command == 'execute':
        execute()
    else:
        request(args.check_run, args.mode, args.dry_run)


if __name__ == '__main__':
    main()
