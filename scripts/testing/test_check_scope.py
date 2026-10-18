"""Scope policy and immutable proof regression; temporary local Git only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/ci'))
import check_scope as scope
import run as runner


class ScopeTests(unittest.TestCase):
    sha = 'a' * 40
    base = 'b' * 40

    def plan(self, *paths, mode='scoped', status='M', tracked=None):
        return scope.classify([{'status': status, 'paths': [p]} for p in paths],
            set(tracked or ['backend/tests/test_ip_location_service.py']),
            sha=self.sha, base=self.base, mode=mode)

    def test_docs_frontend_backend_mixed_and_unknown(self):
        docs = self.plan('docs/guide.md')
        self.assertEqual((docs['tier'], docs['frontend'], docs['backend'], docs['deploy']),
                         ('light', 'none', 'none', False))
        front = self.plan('frontend/src/components/Button.tsx')
        self.assertEqual((front['frontend'], front['backend']), ('related', 'none'))
        self.assertEqual(front['frontend_inputs'], ['src/components/Button.tsx'])
        back = self.plan('backend/services/ip_location_service.py')
        self.assertEqual((back['frontend'], back['backend']), ('none', 'targeted'))
        self.assertEqual(back['backend_tests'], ['tests/test_ip_location_service.py'])
        mixed = self.plan('frontend/src/components/Button.tsx', 'backend/services/ip_location_service.py')
        self.assertEqual((mixed['frontend'], mixed['backend']), ('related', 'targeted'))
        self.assertEqual(self.plan('backend/services/unknown.py')['tier'], 'full')

    def test_core_dependencies_contracts_and_prompt_markdown_are_full(self):
        for path in ('backend/migrations/999_new.sql', 'backend/core/config.py',
                     'backend/api/routes/auth.py', 'backend/services/scheduler/worker.py',
                     'frontend/package-lock.json', 'backend/requirements.txt',
                     'frontend/src/services/chat.ts', 'frontend/src/schemas/message.ts',
                     'frontend/src/hooks/usePermission.ts', 'frontend/src/test/setup.ts',
                     'frontend/src/config/featureFlags.ts', 'frontend/src/constants/task.ts',
                     '.github/workflows/pre-release-checks.yml', 'scripts/ci/run.py',
                     'deploy/release.sh', 'AGENTS.md',
                     'backend/services/prompt_builder/templates/workflow.md'):
            with self.subTest(path=path):
                self.assertEqual(self.plan(path)['tier'], 'full')

    def test_missing_mapping_expands_and_runtime_deletion_is_full(self):
        self.assertEqual(self.plan('backend/services/ip_location_service.py', tracked=['other'])['tier'], 'full')
        self.assertEqual(self.plan('frontend/src/components/Button.tsx', status='D')['tier'], 'full')
        self.assertEqual(self.plan('docs/guide.md', status='D')['tier'], 'light')
        changes = scope.parse_changes(b'R100\0frontend/src/Old.tsx\0docs/new.md\0')
        self.assertEqual(scope.classify(changes, set(), sha=self.sha, base=self.base)['tier'], 'full')

    def test_sensitive_and_malformed_paths_fail_closed(self):
        for path in ('backend/.env', 'deploy/config.env', 'id_ed25519', 'server.pem',
                     '../escape', '/absolute', 'name\nline', '-option'):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.plan(path)
        for raw in (b'M\0missing', b'R100\0one\0', b'U\0file\0'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                scope.parse_changes(raw)

    def test_shadow_and_full_only_strengthen(self):
        for mode in ('shadow', 'full'):
            plan = self.plan('frontend/src/components/Button.tsx', mode=mode)
            self.assertEqual(plan['proposed'], {'frontend': 'related', 'backend': 'none'})
            self.assertEqual((plan['frontend'], plan['backend']), ('full', 'full'))
        self.assertEqual(scope.requested_mode('refs/tags/preflight/full/task'), 'full')
        self.assertEqual(scope.requested_mode('refs/tags/preflight/shadow/task'), 'shadow')

    def test_verification_requests_are_explicit_and_strict(self):
        ref = 'refs/tags/preflight/verify/' + self.base + '/frontend/012345abcdef'
        self.assertEqual(scope.verification_request(ref), (self.base, 'frontend'))
        self.assertIsNone(scope.verification_request('refs/tags/preflight/regular'))
        with self.assertRaises(RuntimeError): scope.verification_request(ref + '/extra')

    def test_plan_binds_sha_base_policy_mode_and_selected_tests(self):
        plan = self.plan('frontend/src/components/Button.tsx')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(plan))
            self.assertEqual(scope.load_plan(path, self.sha), plan)
            with self.assertRaises(RuntimeError):
                scope.load_plan(path, 'c' * 40)
            for key, value in [('policy', 'old'), ('base', 'c' * 40), ('mode', 'full'), ('frontend', 'none')]:
                path.write_text(json.dumps({**plan, key: value}))
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    scope.load_plan(path, self.sha)

    def test_proof_requires_classification_and_selected_jobs_not_skipped(self):
        plan = self.plan('frontend/src/components/Button.tsx')
        proof = f"required / {self.base} / scoped / {plan['fingerprint']}"
        jobs = [{'name': name, 'status': 'completed', 'conclusion': state} for name, state in
                [('scope', 'success'), ('frontend', 'success'), ('backend', 'skipped'), (proof, 'success')]]
        with patch.object(scope, 'make_plan', return_value=plan):
            self.assertEqual(scope.proof_plan(jobs, self.sha), plan)
            for name in ('scope', 'frontend', proof):
                for state in ('skipped', 'failure', 'cancelled'):
                    bad = [{**j, 'conclusion': state} if j['name'] == name else j for j in jobs]
                    with self.subTest(name=name, state=state), self.assertRaises(RuntimeError):
                        scope.proof_plan(bad, self.sha)
            for bad in (jobs[:-1], jobs + [jobs[-1]], jobs[1:]):
                with self.assertRaises(RuntimeError):
                    scope.proof_plan(bad, self.sha)
            stale = [{**j, 'name': proof[:-1] + ('0' if proof[-1] != '0' else '1')} if j['name'] == proof else j for j in jobs]
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                scope.proof_plan(stale, self.sha)

    def test_actual_git_diff_covers_all_commits_and_both_rename_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-b', 'main'); git('config', 'user.name', 'scope-test')
            git('config', 'user.email', 'scope@example.invalid')
            (root / 'docs').mkdir(); (root / 'docs/a.md').write_text('initial\n')
            (root / 'frontend/src').mkdir(parents=True)
            (root / 'frontend/src/old.ts').write_text('export const v = 1\n')
            git('add', '.'); git('commit', '-m', 'base'); base = git('rev-parse', 'HEAD')
            (root / 'frontend/src/old.ts').write_text('export const v = 2\n')
            git('add', '.'); git('commit', '-m', 'first runtime change')
            (root / 'docs/a.md').write_text('last commit only docs\n')
            git('add', '.'); git('commit', '-m', 'docs')
            plan = scope.make_plan(git('rev-parse', 'HEAD'), base, root=root)
            self.assertEqual(plan['frontend'], 'related')
            git('mv', 'frontend/src/old.ts', 'docs/moved.md'); git('commit', '-m', 'rename')
            self.assertEqual(scope.make_plan(git('rev-parse', 'HEAD'), base, root=root)['tier'], 'full')
            with self.assertRaises(subprocess.CalledProcessError):
                scope.make_plan(base, git('rev-parse', 'HEAD'), root=root)


class RunnerScopeTests(unittest.TestCase):
    def frontend(self, selected, uncovered=None):
        class FakeChecks:
            report = {'plan': {'frontend': 'related', 'frontend_inputs': ['src/example.ts']}}
            calls = []
            def run(self, name, command, **kwargs):
                self.calls.append((name, command))
                if name == 'related-selection':
                    (runner.STATE / 'frontend-related.json').write_text(json.dumps({'tests': selected, 'uncovered': uncovered if uncovered is not None else [] if selected else ['src/example.ts']}))
        checks = FakeChecks()
        runner.frontend(checks)
        return checks

    def test_frontend_uses_related_tests_and_empty_selection_expands(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'STATE', Path(directory)):
            related = self.frontend(['src/example.test.ts'])
            self.assertIn('related', related.calls[2][1])
            self.assertEqual(related.calls[-1], ('build', ['npm', 'run', 'build']))
            fallback = self.frontend([])
            self.assertNotIn('related', fallback.calls[2][1])
            self.assertIn('fallback', fallback.report)
            partial = self.frontend(['src/example.test.ts'], uncovered=['src/untested.ts'])
            self.assertNotIn('related', partial.calls[2][1])
            self.assertIn('fallback', partial.report)

    def test_targeted_backend_does_not_bootstrap_or_start_services(self):
        class FakeChecks:
            report = {'plan': {'backend': 'targeted', 'backend_tests': ['tests/test_ip_location_service.py']}}
            def __init__(self): self.calls = []
            def run(self, name, command, **kwargs): self.calls.append((name, command))
        checks = FakeChecks()
        with patch.object(runner, 'ROOT', Path('/workspace/fresh-scoped-ci')), \
             patch.object(runner.os, 'geteuid', return_value=1001), \
             patch.dict(os.environ, {}, clear=True):
            runner.backend(checks)
        names = [name for name, command in checks.calls]
        self.assertIn('syntax', names)
        self.assertIn('regression', names)
        self.assertNotIn('empty-bootstrap', names)
        self.assertNotIn('native-tools', names)
        self.assertIn('tests/test_ip_location_service.py', checks.calls[-1][1][-1])

    def test_retry_only_transient_dependency_failure_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'STATE', Path(directory)), patch.dict(os.environ, {'CHECKED_SHA': 'a' * 40}):
            counter = Path(directory) / 'attempts'
            script = ("from pathlib import Path; p=Path(" + repr(str(counter)) + "); "
                      "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); "
                      "print('EAI_AGAIN'); raise SystemExit(1)")
            checks = runner.Checks('frontend')
            with self.assertRaises(RuntimeError):
                checks.run('dependencies', [sys.executable, '-c', script], retry_network=True)
            self.assertEqual(counter.read_text(), '2')
            self.assertEqual(len(checks.report['phases']), 2)
            counter.unlink()
            with self.assertRaises(RuntimeError):
                checks.run('tests', [sys.executable, '-c', script])
            self.assertEqual(counter.read_text(), '1')
            checks = runner.Checks('frontend')
            with self.assertRaises(RuntimeError):
                checks.run('dependencies', [sys.executable, '-c', 'print("assertion failed"); raise SystemExit(1)'], retry_network=True)
            self.assertEqual(len(checks.report['phases']), 1)


if __name__ == '__main__':
    unittest.main()
