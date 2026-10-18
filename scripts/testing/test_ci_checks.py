"""CI safety boundaries; local subprocesses only, no real remote or services."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runner = module('ci_runner', 'scripts/ci/run.py')
request = module('ci_request', 'scripts/ci/request-check.py')


class PipelineTests(unittest.TestCase):
    def test_workflow_is_readonly_and_has_no_production_steps(self):
        workflow = yaml.load((ROOT / '.github/workflows/pre-release-checks.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertNotIn('pull_request_target', workflow['on'])
        self.assertEqual(workflow['on']['push']['tags'], ['preflight/**'])
        for job in workflow['jobs'].values():
            self.assertNotIn('environment', job)
            self.assertLessEqual(int(job['timeout-minutes']), 30)
            for step in job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'], r'@[0-9a-f]{40}$')
                self.assertNotIn('secrets.', json.dumps(step))
                command = step.get('run', '')
                self.assertNotIn('deploy/release.sh', command)
                self.assertNotIn('deploy/deploy.sh', command)
                self.assertNotIn('ssh ', command)
        backend = workflow['jobs']['backend']
        archive = next(s['run'] for s in backend['steps'] if 'Copy only' in s.get('name', ''))
        self.assertIn('git archive HEAD', archive)
        self.assertIn('runuser -u ci', json.dumps(backend))

    def test_sha_is_complete_and_not_an_expression(self):
        with patch.dict(os.environ, {'CHECKED_SHA': 'a' * 40}):
            self.assertEqual(runner.checked_sha(), 'a' * 40)
        for value in ('main', 'a' * 39, '$(must-not-run)', '../other'):
            with self.subTest(value=value), patch.dict(os.environ, {'CHECKED_SHA': value}):
                with self.assertRaises(RuntimeError):
                    runner.checked_sha()

    def test_private_logs_do_not_enter_report(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'STATE', Path(directory)), patch.dict(os.environ, {'CHECKED_SHA': 'a' * 40}):
            checks = runner.Checks('frontend')
            checks.run('fake', [sys.executable, '-c', "print('synthetic-private-value'); print('\\x1b[32m242\\x1b[0m passed')"])
            report = (Path(directory) / 'frontend.json').read_text()
            self.assertNotIn('synthetic-private-value', report)
            self.assertIn('242 passed', report)
            self.assertEqual((Path(directory) / 'frontend-fake.log').stat().st_mode & 0o777, 0o600)

    def test_failed_phase_stops_and_is_reported(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'STATE', Path(directory)), patch.dict(os.environ, {'CHECKED_SHA': 'a' * 40}):
            checks = runner.Checks('backend')
            with self.assertRaises(RuntimeError):
                checks.run('failure', [sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertEqual(checks.report['phases'][0]['exit_code'], 7)
            self.assertEqual(checks.report['phases'][0]['status'], 'failed')

    def test_timeout_stops_its_process_group(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'STATE', Path(directory)), patch.dict(os.environ, {'CHECKED_SHA': 'a' * 40}):
            checks = runner.Checks('backend')
            with self.assertRaises(RuntimeError):
                checks.run('timeout', [sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.1)
            self.assertEqual(checks.report['phases'][0]['status'], 'timeout')
            self.assertEqual(checks.report['phases'][0]['exit_code'], 124)

    def test_request_never_pushes_dirty_unpushed_or_main_checkout(self):
        for branch, dirty, remote in [('main', '', 'a' * 40), ('codex/task/a', ' M product.py', 'a' * 40), ('codex/task/a', '', 'b' * 40)]:
            def fake_git(*args):
                return {('branch', '--show-current'): branch,
                        ('status', '--porcelain', '--untracked-files=normal'): dirty,
                        ('rev-parse', 'HEAD'): 'a' * 40}.get(args, remote + '\trefs/heads/' + branch)
            with patch.object(request, 'git', side_effect=fake_git), patch.object(request.subprocess, 'run') as run, patch.object(sys, 'argv', ['request-check.py']):
                with self.assertRaises(RuntimeError):
                    request.main()
                run.assert_not_called()

    def test_request_uses_new_tag_without_force_or_task_branch_write(self):
        def fake_git(*args):
            return {('branch', '--show-current'): 'codex/task/a',
                    ('status', '--porcelain', '--untracked-files=normal'): '',
                    ('rev-parse', 'HEAD'): 'a' * 40}.get(args, 'a' * 40 + '\trefs/heads/codex/task/a')
        with patch.object(request, 'git', side_effect=fake_git), patch.object(request.subprocess, 'run') as run, patch.object(sys, 'argv', ['request-check.py']):
            request.main()
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ['git', 'push', 'origin'])
            self.assertRegex(command[3], r'^a{40}:refs/tags/preflight/[0-9a-f]{16}/a{40}/')
            self.assertNotIn('--force', command)


if __name__ == '__main__':
    unittest.main()
