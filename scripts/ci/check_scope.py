#!/usr/bin/env python3
"""Deterministic, conservative check policy, bound to the whole candidate diff."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SHA = r'[0-9a-f]{40}'
MODES = ('scoped', 'shadow', 'full')
# Only reviewed mock-only suites belong here. Unmapped backend code is full.
BACKEND_MAP = {
    'backend/services/ip_location_service.py': ['tests/test_ip_location_service.py'],
}
PROOF = re.compile(r'required / (' + SHA + r') / (scoped|shadow|full) / ([0-9a-f]{64})')


def git(*args, root=ROOT):
    return subprocess.check_output(['git', *args], cwd=root)


def policy_digest():
    # Changing selection, execution or workflow invalidates cached plans.
    paths = ('scripts/ci/check_scope.py', 'scripts/ci/run.py',
             '.github/workflows/pre-release-checks.yml',
             'frontend/scripts/list-related-tests.mjs')
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.encode() + b'\0' + (ROOT / path).read_bytes())
    return digest.hexdigest()


def fingerprint(plan):
    return hashlib.sha256(json.dumps({k: v for k, v in plan.items() if k != 'fingerprint'},
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def safe_path(path):
    return (isinstance(path, str) and path and not path.startswith(('/', '-'))
            and not any(c in path for c in '\x00\r\n\\')
            and '..' not in PurePosixPath(path).parts)


def sensitive(path):
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return (any(p in {'.git', 'node_modules', '.venv', 'venv'} for p in parts)
            or (name.startswith('.env') and not name.endswith(('.example', '.sample', '.template')))
            or name == 'config.env' or name in {'id_rsa', 'id_ed25519'}
            or name.endswith(('.pem', '.key', '.p12', '.pfx')))


def documentation(path):
    # Markdown used as prompts/skills or other application input is NOT docs.
    return ((path.startswith('docs/') and path.endswith(('.md', '.txt')))
            or path in {'README.md', 'frontend/README.md', 'backend/README.md'})


def parse_changes(raw):
    tokens = raw.decode('utf-8').split('\0')
    changes = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        index += 1
        count = 2 if status.startswith(('R', 'C')) else 1
        paths = tokens[index:index + count]
        if len(paths) != count or any(not safe_path(p) for p in paths):
            raise RuntimeError('Invalid changed path; classification failed closed')
        if status[0] not in 'AMDRCT':
            raise RuntimeError('Unsupported diff status; classification failed closed')
        changes.append({'status': status, 'paths': paths})
        index += count
    if index != len(tokens) - 1:
        raise RuntimeError('Malformed diff; classification failed closed')
    return changes


def classify(changes, tracked, *, sha, base, mode='scoped'):
    if mode not in MODES or not re.fullmatch(SHA, sha) or not re.fullmatch(SHA, base):
        raise RuntimeError('Invalid scope identity')
    front, backend_tests, reasons = set(), set(), set()
    full = False
    for change in changes:
        for path in change['paths']:
            if not safe_path(path) or sensitive(path):
                raise RuntimeError('Forbidden sensitive or invalid changed path')
            if documentation(path):
                continue
            # Deletion/rename can break imports without appearing in related tests.
            if change['status'][0] not in 'AM':
                full = True
                reasons.add('runtime-delete-rename-or-type-change')
            elif path.startswith(('frontend/src/', 'frontend/public/')):
                # Shared contracts, auth and test setup are never narrowed.
                if (path.startswith(('frontend/src/services/', 'frontend/src/schemas/',
                                     'frontend/src/stores/', 'frontend/src/contexts/',
                                     'frontend/src/test/', 'frontend/src/types/',
                                     'frontend/src/config/', 'frontend/src/constants/'))
                        or any(word in path.lower() for word in ('auth', 'permission', 'scheduled', 'protocol'))
                        or path in {'frontend/src/main.tsx', 'frontend/src/App.tsx'}):
                    full = True
                    reasons.add('frontend-shared-or-security')
                else:
                    front.add(path)
            elif path in BACKEND_MAP:
                backend_tests.update(BACKEND_MAP[path])
            elif path.startswith('backend/tests/') and any(
                    path == 'backend/' + test for tests in BACKEND_MAP.values() for test in tests):
                backend_tests.add(path.removeprefix('backend/'))
            else:
                full = True
                reasons.add('unmapped-or-core-change')
    if backend_tests and any('backend/' + test not in tracked for test in backend_tests):
        full = True
        reasons.add('backend-test-map-missing')
    tier = 'full' if full else 'regular' if front or backend_tests else 'light'
    proposed = {'frontend': 'full' if full else 'related' if front else 'none',
                'backend': 'full' if full else 'targeted' if backend_tests else 'none'}
    actual = dict(proposed)
    if mode in ('shadow', 'full'):
        actual = {'frontend': 'full', 'backend': 'full'}
    plan = {'version': 1, 'policy': policy_digest(), 'sha': sha, 'base': base, 'mode': mode,
            'tier': tier, 'proposed': proposed, **actual,
            'frontend_inputs': sorted(p.removeprefix('frontend/') for p in front),
            'backend_tests': sorted(backend_tests), 'reasons': sorted(reasons),
            'deploy': tier != 'light'}
    plan['fingerprint'] = fingerprint(plan)
    return plan


def make_plan(sha, base, *, mode='scoped', root=ROOT):
    if not re.fullmatch(SHA, sha) or not re.fullmatch(SHA, base):
        raise RuntimeError('Scope needs complete candidate and base SHAs')
    subprocess.run(['git', 'merge-base', '--is-ancestor', base, sha], cwd=root, check=True)
    # NUL paths and both rename sides ensure spaces/deletions cannot disappear.
    changes = parse_changes(git('diff', '--name-status', '-z', '--find-renames', base, sha, root=root))
    subprocess.run(['git', 'diff', '--check', base, sha], cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tracked = set(git('ls-tree', '-r', '--name-only', '-z', sha, root=root).decode().split('\0'))
    return classify(changes, tracked, sha=sha, base=base, mode=mode)


def load_plan(path, sha):
    plan = json.loads(Path(path).read_text())
    if (plan.get('version') != 1 or plan.get('sha') != sha
            or plan.get('policy') != policy_digest() or plan.get('mode') not in MODES
            or plan.get('fingerprint') != fingerprint(plan)):
        raise RuntimeError('Check plan differs from candidate or current policy')
    for kind, allowed in [('frontend', {'none', 'full', 'related'}),
                          ('backend', {'none', 'full', 'targeted'})]:
        if plan.get(kind) not in allowed:
            raise RuntimeError('Invalid check scope')
    if any(not safe_path(p) for p in plan['frontend_inputs'] + plan['backend_tests']):
        raise RuntimeError('Invalid test paths')
    if plan['frontend'] == 'related' and not plan['frontend_inputs']:
        raise RuntimeError('Related checks need source inputs')
    if plan['backend'] == 'targeted' and not plan['backend_tests']:
        raise RuntimeError('Targeted checks need test coverage')
    return plan


def proof_plan(jobs, sha, *, root=ROOT):
    proofs = [job for job in jobs if job['name'].startswith('required / ')]
    if len(proofs) != 1:
        raise RuntimeError('Exactly one successful scope proof is required')
    proof = proofs[0]
    match = PROOF.fullmatch(proof['name'])
    if not match or proof['status'] != 'completed' or proof['conclusion'] != 'success':
        raise RuntimeError('Scope gate failed, missing or skipped')
    base, mode, digest = match.groups()
    plan = make_plan(sha, base, mode=mode, root=root)
    if digest != plan['fingerprint']:
        raise RuntimeError('Scope proof differs from candidate diff or check policy')
    for name in ('scope', 'frontend', 'backend'):
        matched = [job for job in jobs if job['name'] == name]
        required = name == 'scope' or plan[name] != 'none'
        expected = {'success'} if required else {'skipped', 'success'}
        if (len(matched) != 1 or matched[0]['status'] != 'completed'
                or matched[0]['conclusion'] not in expected):
            raise RuntimeError('Required check missing, failed, cancelled or unexpectedly skipped: ' + name)
    return plan


def requested_mode(ref, manual='scoped'):
    if ref.startswith('refs/tags/preflight/full/'):
        return 'full'
    if ref.startswith('refs/tags/preflight/shadow/'):
        return 'shadow'
    if manual not in MODES:
        raise RuntimeError('Invalid check mode')
    return manual


def verification_request(ref):
    if not ref.startswith('refs/tags/preflight/verify/'):
        return None
    match = re.fullmatch(r'refs/tags/preflight/verify/(' + SHA + r')/([a-z-]+)/[0-9a-f]{12}', ref)
    if not match:
        raise RuntimeError('Malformed verification-only request')
    return match.groups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha', default='HEAD')
    parser.add_argument('--base', default='origin/main')
    parser.add_argument('--mode', choices=MODES, default=None)
    parser.add_argument('--output', default='.cloud-dev/ci/check-scope.json')
    args = parser.parse_args()
    sha = git('rev-parse', args.sha + '^{commit}').decode().strip()
    verification = verification_request(os.environ.get('GITHUB_REF', ''))
    base = git('rev-parse', args.base + '^{commit}').decode().strip()
    if verification:
        base, scenario = verification
        parents = git('rev-list', '--parents', '-n', '1', sha).decode().split()
        if parents != [sha, base]:
            raise RuntimeError('Verification candidate must be a direct child of its isolated base')
    mode = args.mode or requested_mode(os.environ.get('GITHUB_REF', ''), os.environ.get('CHECK_MODE') or 'scoped')
    if verification and verification[1] == 'shadow':
        mode = 'shadow'
    plan = make_plan(sha, base, mode=mode)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, indent=2) + '\n')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            for key in ('base', 'mode', 'fingerprint', 'frontend', 'backend'):
                stream.write(f'{key}={plan[key]}\n')
    summary = (f"CHECK_SCOPE tier={plan['tier']} mode={mode} frontend={plan['frontend']} "
               f"backend={plan['backend']} deploy={plan['deploy']} sha={sha}\n")
    print(summary + json.dumps(plan, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('```\n' + summary + '```\n\nProposed: ' + json.dumps(plan['proposed']) + '\n')


if __name__ == '__main__':
    main()
