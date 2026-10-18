#!/usr/bin/env python3
"""Credential-free pre-release checks with bounded phases and safe reports."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

import check_scope

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.cloud-dev' / 'ci'
BACKEND_TESTS = [
    'test_local_db_async.py', 'test_local_db_rpc.py', 'test_websocket_redis.py',
    'test_org_scoped_db.py', 'test_org_scoped_db_isolation.py',
    'test_org_scoped_db_upsert.py', 'test_permissions_initialization.py',
    'test_auth_service.py', 'test_conversation_service.py', 'test_task_recovery.py',
    'test_message_gateway.py',
]


def checked_sha():
    value = os.environ.get('CHECKED_SHA') or subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if not re.fullmatch('[0-9a-f]{40}', value):
        raise RuntimeError('Expected a complete commit SHA')
    return value


def stop_process(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


class Checks:
    def __init__(self, kind):
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(STATE, 0o700)
        self.kind = kind
        self.report = {'kind': kind, 'commit': checked_sha(), 'status': 'running',
                       'scope': 'isolated development only; no deployment', 'phases': []}
        self.started = time.monotonic()

    def save(self):
        self.report['elapsed_seconds'] = round(time.monotonic() - self.started, 2)
        (STATE / f'{self.kind}.json').write_text(json.dumps(self.report, indent=2) + '\n')

    def run(self, name, command, *, cwd=ROOT, env=None, timeout=600, retry_network=False):
        started = time.monotonic()
        entry = {'name': name, 'status': 'running'}
        self.report['phases'].append(entry)
        print(f'CHECK_PHASE started={name}', flush=True)
        self.save()
        with (STATE / f'{self.kind}-{name}.log').open('w') as log:
            os.chmod(log.name, 0o600)
            child = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
            try:
                result = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_process(child)
                entry['status'] = 'timeout'
                result = 124
            except BaseException:
                stop_process(child)
                raise
        entry.update(status='passed' if result == 0 else entry.get('status', 'failed'),
                     exit_code=result, seconds=round(time.monotonic() - started, 2))
        if result and entry['status'] == 'running':
            entry['status'] = 'failed'
        # Only allowlisted test totals leave private logs; never publish traces,
        # connection errors, .env, synthetic identities or native data directories.
        text = Path(log.name).read_text(errors='replace')
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
        totals = re.findall(r'(?<!\w)\d+ (?:passed|failed|skipped)|Ran \d+ tests', text)
        if totals:
            entry['test_totals'] = totals[-8:]
        self.save()
        print(f'CHECK_PHASE result={entry["status"]} name={name} seconds={entry["seconds"]}', flush=True)
        if result:
            network_error = re.search(r'\b(?:EAI_AGAIN|ECONNRESET|ETIMEDOUT|ECONNREFUSED|ENETUNREACH|ENOTFOUND)\b|Temporary failure in name resolution|ReadTimeoutError|ConnectTimeoutError', text)
            if retry_network and result != 124 and network_error:
                entry['retry'] = 'one-transient-network-retry'
                self.save()
                print(f'CHECK_RETRY phase={name} reason=transient-network attempts=1', flush=True)
                return self.run(name + '-retry', command, cwd=cwd, env=env, timeout=timeout)
            raise RuntimeError(f'{name} failed (exit {result}); raw log remains private')


def frontend(checks):
    directory = ROOT / 'frontend'
    checks.run('dependencies', ['npm', 'ci', '--no-audit', '--no-fund'], cwd=directory, retry_network=True)
    plan = checks.report.get('plan') or {'frontend': 'full'}
    command = ['npm', 'run', 'test:run', '--', '--maxWorkers=4']
    if plan['frontend'] == 'related':
        inputs = plan['frontend_inputs']
        listing = STATE / 'frontend-related.json'
        # Discovery failures block. Any uncovered input expands to all tests.
        checks.run('related-selection', ['node', 'scripts/list-related-tests.mjs', str(listing), *inputs], cwd=directory)
        selected = json.loads(listing.read_text())
        if (not isinstance(selected, dict) or not isinstance(selected.get('tests'), list)
                or not isinstance(selected.get('uncovered'), list)):
            raise RuntimeError('Invalid related test discovery')
        if selected['tests'] and not selected['uncovered']:
            # Vitest resolves transitive importers; include changed tests themselves.
            command = ['npm', 'exec', '--', 'vitest', 'related', *inputs, '--run', '--maxWorkers=4']
        else:
            checks.report['fallback'] = 'no-related-tests: full-frontend-tests'
    checks.run('tests', command, cwd=directory)
    checks.run('build', ['npm', 'run', 'build'], cwd=directory)


def backend(checks):
    # This path only works in the disposable non-root Linux checkout. There is
    # intentionally no option for supplying another database or connection URL.
    if sys.platform != 'linux' or os.geteuid() == 0 or not ROOT.is_relative_to('/workspace'):
        raise RuntimeError('Use a non-root disposable Linux checkout under /workspace')
    if (ROOT / 'backend/.env').exists():
        raise RuntimeError('Pre-release database checks require a fresh checkout without .env')
    for key in ('DATABASE_URL', 'JWT_SECRET_KEY', 'REDIS_URL', 'REDIS_HOST', 'REDIS_PASSWORD'):
        if os.environ.get(key):
            raise RuntimeError('Refusing inherited application connection configuration')
    env = dict(os.environ)
    checks.run('venv', [sys.executable, '-m', 'venv', '.venv'])
    python = str(ROOT / '.venv/bin/python')
    checks.run('dependencies', [python, '-m', 'pip', 'install', '-r', 'backend/requirements.txt'], timeout=900, retry_network=True)
    checks.run('pip-check', [python, '-m', 'pip', 'check'])
    checks.run('configuration', [python, 'scripts/cloud/dev.py', 'init'])
    checks.run('syntax', [python, '-m', 'compileall', '-q', 'backend'])
    plan = checks.report.get('plan') or {'backend': 'full'}
    if plan['backend'] == 'targeted':
        checks.run('regression', [python, '-c',
            "from dotenv import load_dotenv; load_dotenv('.env', override=True); "
            "import pytest; raise SystemExit(pytest.main(" + repr(
                plan['backend_tests'] + ['-o', 'addopts=', '-q']) + '))'], cwd=ROOT / 'backend')
        return
    checks.run('native-tools', [python, 'scripts/cloud/native.py'])
    services = [python, 'scripts/cloud/services.py']
    managed = False
    child = None
    try:
        # Stop this instance even if prepare fails partway through.
        managed = True
        checks.run('empty-bootstrap', [*services, 'prepare', str(ROOT)])
        checks.run('repeat-bootstrap', [*services, 'prepare', str(ROOT)])
        checks.run('preflight', [python, 'scripts/cloud/dev.py', 'check'])
        checks.run('integration', [python, '-m', 'unittest',
            'scripts/testing/test_cloud_bootstrap.py', 'scripts/testing/test_cloud_development.py',
            'scripts/testing/test_migration_executor.py', '-v'],
            env={**env, 'CLOUD_NATIVE_INTEGRATION': '1'})
        # Loading only the generated, checkout-local .env prevents accidental use
        # of the runner's configuration. No external provider secrets are supplied.
        checks.run('regression', [python, '-c',
            "from dotenv import load_dotenv; load_dotenv('.env', override=True); "
            "import pytest; raise SystemExit(pytest.main(" + repr(
                list(dict.fromkeys(['tests/' + name for name in BACKEND_TESTS] + plan.get('backend_tests', []))) + ['-o', 'addopts=', '-q']) + '))'],
            cwd=ROOT / 'backend')
        checks.run('pipeline-guards', [python, '-m', 'unittest',
            'scripts/testing/test_ci_checks.py', 'scripts/testing/test_check_scope.py', 'scripts/testing/test_delivery.py',
            'scripts/testing/test_release_coordination.py', 'scripts/testing/test_ssh_gateway.py', 'scripts/testing/test_task_delivery.py', '-v'])
        # Real backend smoke without installing a second copy of frontend deps.
        # The frontend is tested/built by a parallel independent job.
        (ROOT / '.cloud-dev/readiness.json').write_text(json.dumps({'backend_log_offset': 0}))
        log = (ROOT / '.cloud-dev/backend.log').open('w')
        os.chmod(log.name, 0o600)
        launcher = "from dotenv import load_dotenv; load_dotenv('.env', override=True); import uvicorn; uvicorn.run('main:app', host='127.0.0.1', port=8000)"
        child = subprocess.Popen([python, '-c', launcher], cwd=ROOT / 'backend', env=env,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        for _ in range(90):
            if child.poll() is not None:
                raise RuntimeError('API exited before readiness')
            try:
                with urlopen('http://127.0.0.1:8000/api/health/db', timeout=2) as response:
                    if json.load(response).get('status') == 'ok':
                        break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('API readiness timeout')
        checks.report['phases'].append({'name': 'api-start', 'status': 'passed',
                                        'seconds': round(time.monotonic() - started, 2)})
        checks.run('smoke', [python, 'scripts/cloud/smoke.py'], timeout=120)
    finally:
        if child is not None:
            stop_process(child)
        if managed:
            checks.run('cleanup', [*services, 'stop', str(ROOT)], timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('frontend', 'backend'))
    parser.add_argument('--plan')
    args = parser.parse_args()
    checks = Checks(args.kind)
    if args.plan:
        plan = check_scope.load_plan(args.plan, checked_sha())
        if plan[args.kind] == 'none':
            raise RuntimeError('Unselected job must not run as a successful required check')
        checks.report['plan'] = plan
    try:
        {'frontend': frontend, 'backend': backend}[args.kind](checks)
        checks.report['status'] = 'passed'
    except BaseException as error:
        checks.report['status'] = 'failed'
        checks.report['error_type'] = type(error).__name__
        print(f'CHECK_RESULT failed kind={args.kind}; see phase totals in report', flush=True)
        raise
    finally:
        checks.save()
        print(json.dumps(checks.report, indent=2), flush=True)


if __name__ == '__main__':
    main()
