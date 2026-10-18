#!/usr/bin/env python3
"""Run real preflight jobs on disposable candidate tags; never request delivery."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import check_scope
import delivery

ROOT = delivery.ROOT
REPOSITORY = os.environ.get('GITHUB_REPOSITORY', 'wucong75644-crypto/EVERYDAYAIONE')
STATE = ROOT / '.cloud-dev/ci/scope-verification.json'
SCENARIOS = {
    'docs': ('light', 'none', 'none'),
    'frontend': ('regular', 'related', 'none'),
    'backend': ('regular', 'none', 'targeted'),
    'mixed': ('regular', 'related', 'targeted'),
    'core': ('full', 'full', 'full'),
    'unknown': ('full', 'full', 'full'),
    'shadow': ('regular', 'full', 'full'),
    'failed-test': ('regular', 'related', 'none'),
    'failed-scope': None,
}


def git(*args, env=None, data=None):
    return subprocess.check_output(['git', *args], cwd=ROOT, env=env, input=data)


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + '\n')


def create(push):
    if STATE.exists():
        raise RuntimeError('Verification state exists; resume poll rather than duplicate requests')
    if delivery.git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Commit exact task files before creating verification candidates')
    base = delivery.git('rev-parse', 'HEAD')
    branch = delivery.git('branch', '--show-current')
    if not branch.startswith('codex/task/'):
        raise RuntimeError('Use the original task checkout')
    remote = delivery.git('ls-remote', '--exit-code', 'origin', 'refs/heads/' + branch).split()
    if not remote or remote[0] != base:
        raise RuntimeError('Task base must already be pushed and exact')
    state = {'base': base, 'branch': branch, 'repository': REPOSITORY, 'scenarios': {}}
    for scenario in SCENARIOS:
        changes = {}
        if scenario == 'docs':
            changes['docs/testing/scope-verification-fixture.md'] = b'Isolated scope verification only.\n'
        if scenario in ('frontend', 'mixed', 'shadow'):
            path = 'frontend/src/hooks/useTheme.ts'
            changes[path] = git('show', base + ':' + path) + b'\n// Isolated scope verification.\n'
        if scenario in ('backend', 'mixed'):
            path = 'backend/services/ip_location_service.py'
            changes[path] = git('show', base + ':' + path) + b'\n# Isolated scope verification.\n'
        if scenario == 'core':
            path = 'frontend/src/config/task.ts'
            changes[path] = git('show', base + ':' + path) + b'\n// Isolated core scope verification.\n'
        if scenario == 'unknown':
            changes['scope-verification-fixture.txt'] = b'Unknown path must select full checks.\n'
        if scenario == 'failed-test':
            path = 'frontend/src/hooks/__tests__/useTheme.test.ts'
            changes[path] = git('show', base + ':' + path) + b"\nit('intentional verification failure', () => { throw new Error('EXPECTED_SCOPE_VERIFICATION_FAILURE'); });\n"
        if scenario == 'failed-scope':
            # This file contains no credential; it tests forbidden-path handling.
            changes['docs/.env.scope-verification'] = b'SYNTHETIC_NO_CREDENTIALS=true\n'
        with tempfile.TemporaryDirectory(prefix='scope-index-') as directory:
            env = {**os.environ, 'GIT_INDEX_FILE': str(Path(directory) / 'index')}
            git('read-tree', base, env=env)
            for path, content in changes.items():
                blob = git('hash-object', '-w', '--stdin', data=content).decode().strip()
                git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + path, env=env)
            tree = git('write-tree', env=env).decode().strip()
            sha = git('commit-tree', tree, '-p', base, data=('test: isolated scope ' + scenario + '\n').encode()).decode().strip()
        tag = f'preflight/verify/{base}/{scenario}/{uuid4().hex[:12]}'
        state['scenarios'][scenario] = {'sha': sha, 'tag': tag, 'status': 'prepared'}
    save(state)
    if push:
        # Only these audit tags are written; no branch, main, delivery or server writes.
        refs = [s['sha'] + ':refs/tags/' + s['tag'] for s in state['scenarios'].values()]
        subprocess.run(['git', 'push', 'origin', *refs], cwd=ROOT, check=True)
        for item in state['scenarios'].values(): item['status'] = 'requested'
        save(state)
    print(json.dumps(state, indent=2))


def job(jobs, name):
    found = [j for j in jobs if j['name'] == name or (name == 'required' and j['name'].startswith('required / '))]
    if len(found) != 1:
        raise RuntimeError('Missing or duplicate actual job: ' + name)
    return found[0]['conclusion']


def validate(scenario, item, run):
    sha = item['sha']
    if run['head_sha'] != sha or run['head_branch'] != item['tag']:
        raise RuntimeError('Actual run does not bind the exact verification candidate')
    jobs = delivery.api(REPOSITORY, 'actions/runs/' + str(run['id']) + '/jobs?filter=latest&per_page=100')['jobs']
    conclusions = {name: job(jobs, name) for name in ('scope', 'frontend', 'backend', 'required')}
    negative = scenario in ('failed-test', 'failed-scope')
    if run['conclusion'] != ('failure' if negative else 'success'):
        raise RuntimeError('Unexpected workflow conclusion for ' + scenario + ': ' + str(conclusions))
    if scenario == 'failed-scope':
        assert conclusions == {'scope': 'failure', 'frontend': 'skipped', 'backend': 'skipped', 'required': 'failure'}, conclusions
    else:
        base = json.loads(STATE.read_text())['base']
        mode = 'shadow' if scenario == 'shadow' else 'scoped'
        plan = check_scope.make_plan(sha, base, mode=mode)
        assert (plan['tier'], plan['frontend'], plan['backend']) == SCENARIOS[scenario], plan
        expected = {'scope': 'success', 'required': 'failure' if negative else 'success'}
        for kind in ('frontend', 'backend'):
            expected[kind] = 'skipped' if plan[kind] == 'none' else 'failure' if negative and kind == 'frontend' else 'success'
        assert conclusions == expected, conclusions
        if not negative:
            check_scope.proof_plan(jobs, sha)
            # Use real GitHub job metadata to verify missing/skipped checks fail closed.
            for kind in ('scope', 'frontend', 'backend'):
                if kind != 'scope' and plan[kind] == 'none': continue
                for mutated in ([j for j in jobs if j['name'] != kind],
                                [{**j, 'conclusion': 'skipped'} if j['name'] == kind else j for j in jobs]):
                    try: check_scope.proof_plan(mutated, sha)
                    except RuntimeError: pass
                    else: raise RuntimeError('Missing/skipped required check was accepted')
    for candidate in (sha, json.loads(STATE.read_text())['base']):
        try: delivery.verify_checks(REPOSITORY, run['id'], candidate)
        except RuntimeError: pass
        else: raise RuntimeError('Verification or mismatched SHA was accepted as a production check')
    item.update(status='verified', run=run['id'], url=run['html_url'], conclusion=run['conclusion'], jobs=conclusions,
                created_at=run['created_at'], updated_at=run['updated_at'])


def poll():
    state = json.loads(STATE.read_text())
    runs = delivery.api(REPOSITORY, 'actions/runs?per_page=100')['workflow_runs']
    for scenario, item in state['scenarios'].items():
        if item['status'] == 'verified': continue
        matched = [r for r in runs if r['path'] == '.github/workflows/pre-release-checks.yml'
                   and r['head_branch'] == item['tag'] and r['head_sha'] == item['sha']]
        if not matched: continue
        run = max(matched, key=lambda r: r['id'])
        item.update(run=run['id'], url=run['html_url'])
        if run['status'] == 'completed':
            validate(scenario, item, run)
        else: item['status'] = run['status']
        save(state)
    print(json.dumps({name: {k: item.get(k) for k in ('status', 'run', 'url', 'jobs')} for name, item in state['scenarios'].items()}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'poll'))
    parser.add_argument('--push', action='store_true')
    args = parser.parse_args()
    if args.action == 'create': create(args.push)
    else: poll()
