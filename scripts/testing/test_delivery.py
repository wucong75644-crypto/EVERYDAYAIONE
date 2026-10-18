"""Production delivery rehearsal: temporary Git, fake SSH, no real data."""
import importlib.util
import json
import os
import io
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import yaml
from scripts.testing import test_release_coordination as fixtures

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('delivery', ROOT / 'scripts/ci/delivery.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class DeliveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        self.repository = 'example/repository'
        self.run = {'repository': {'full_name': self.repository}, 'head_sha': self.sha,
                    'status': 'completed', 'conclusion': 'success',
                    'path': '.github/workflows/pre-release-checks.yml', 'html_url': 'https://example.invalid/check'}
        self.jobs = {'jobs': [{'name': name, 'status': 'completed', 'conclusion': 'success'}
                              for name in ('frontend', 'backend')]}

    def test_public_read_fallback_when_cloud_cli_has_no_login(self):
        result = subprocess.CompletedProcess([], 4, '', 'authentication required')
        with patch.object(delivery.shutil, 'which', return_value='/usr/bin/gh'), \
             patch.object(delivery.subprocess, 'run', return_value=result), \
             patch.object(delivery, 'urlopen', return_value=io.BytesIO(b'{"public":true}')) as read:
            self.assertEqual(delivery.api(self.repository, 'actions/runs/123'), {'public': True})
            self.assertEqual(read.call_args.args[0].full_url, 'https://api.github.com/repos/example/repository/actions/runs/123')

    def test_exact_successful_check_and_both_jobs_required(self):
        with patch.object(delivery, 'api', side_effect=[self.run, self.jobs]):
            self.assertEqual(delivery.verify_checks(self.repository, '123', self.sha), self.run['html_url'])
        for changes in ({'head_sha': 'b' * 40}, {'conclusion': 'failure'}, {'status': 'in_progress'},
                        {'path': '.github/workflows/other.yml'}, {'repository': {'full_name': 'other/repo'}}):
            with self.subTest(changes=changes), patch.object(delivery, 'api', return_value={**self.run, **changes}):
                with self.assertRaises(RuntimeError):
                    delivery.verify_checks(self.repository, '123', self.sha)
        for state in ('skipped', 'cancelled', 'failure'):
            jobs = {'jobs': [self.jobs['jobs'][0], {**self.jobs['jobs'][1], 'conclusion': state}]}
            with self.subTest(state=state), patch.object(delivery, 'api', side_effect=[self.run, jobs]):
                with self.assertRaises(RuntimeError):
                    delivery.verify_checks(self.repository, '123', self.sha)

    def test_workflow_source_is_the_checked_commit(self):
        ref = f'refs/tags/delivery-rehearsal/{self.sha}/123/task-a/012345abcdef'
        self.assertEqual(delivery.parse_request(ref, self.sha)['branch'], 'codex/task/task-a')
        for bad in ('refs/heads/main', ref.replace('123/', '$(command)/'), ref.replace('task-a', '../task-a')):
            with self.assertRaises(RuntimeError):
                delivery.parse_request(bad, self.sha)
        with self.assertRaises(RuntimeError):
            delivery.parse_request(ref, 'b' * 40)

    def test_production_disarmed_before_any_remote_or_secret_access(self):
        ref = f'refs/tags/delivery-production/{self.sha}/123/task-a/012345abcdef'
        with patch.dict(os.environ, {'GITHUB_REF': ref, 'GITHUB_SHA': self.sha,
                        'GITHUB_REPOSITORY': self.repository, 'PRODUCTION_DELIVERY_ENABLED': 'false'}), patch.object(delivery, 'api') as api:
            with self.assertRaisesRegex(RuntimeError, 'disarmed'):
                delivery.make_plan()
            with self.assertRaisesRegex(RuntimeError, 'disarmed'):
                delivery.execute()
            api.assert_not_called()

    def test_approval_cannot_be_removed_or_admin_bypassed(self):
        environment = {'can_admins_bypass': False, 'protection_rules': [
            {'type': 'required_reviewers', 'reviewers': [{'type': 'User', 'reviewer': {'login': 'owner'}}]}]}
        with patch.object(delivery, 'api', return_value=environment):
            delivery.verify_environment(self.repository, 'production', 'owner')
        for value in ({**environment, 'can_admins_bypass': True}, {'protection_rules': []}):
            with patch.object(delivery, 'api', return_value=value), self.assertRaises(RuntimeError):
                delivery.verify_environment(self.repository, 'production', 'owner')
        for reviewers in ([{'type': 'Team', 'reviewer': {'login': 'owner'}}],
                          [{'type': 'User', 'reviewer': {'login': 'owner'}},
                           {'type': 'User', 'reviewer': {'login': 'other'}}]):
            value = {'can_admins_bypass': False, 'protection_rules': [
                {'type': 'required_reviewers', 'reviewers': reviewers}]}
            with patch.object(delivery, 'api', return_value=value), self.assertRaises(RuntimeError):
                delivery.verify_environment(self.repository, 'production', 'owner')

    def test_changed_task_ref_is_rejected_after_approval(self):
        with patch.object(delivery, 'git', side_effect=[self.sha, 'b' * 40 + '\trefs/heads/codex/task/a']):
            with self.assertRaisesRegex(RuntimeError, 'changed after request'):
                delivery.verify_source({'sha': self.sha, 'branch': 'codex/task/a'})

    def test_config_rejects_database_secrets_and_shell_metacharacters(self):
        metadata = dict(SERVER_HOST='example.invalid', SERVER_USER='deploy', SERVER_PORT='22',
            REMOTE_APP_DIR='/var/www/everydayai', REMOTE_FRONTEND_DIR='/var/www/everydayai/frontend',
            REMOTE_BACKEND_DIR='/var/www/everydayai/backend', DOMAIN='example.invalid', RUN_MIGRATIONS='false')
        self.assertIn('SERVER_HOST=example.invalid', delivery.config_text(json.dumps(metadata)))
        for changes in ({'DATABASE_URL': 'synthetic'}, {'SERVER_HOST': 'host;command'},
                        {'SERVER_USER': '-oProxyCommand=anything'}, {'SERVER_PORT': '0'},
                        {'REMOTE_APP_DIR': '/../escape'}, {'REMOTE_APP_DIR': '/var/www/other'},
                        {'DOMAIN': 'example;command'}, {'RUN_MIGRATIONS': 'anything'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                delivery.config_text(json.dumps({**metadata, **changes}))

    def test_queue_and_environment_boundaries(self):
        workflow = yaml.load((ROOT / '.github/workflows/delivery.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['concurrency']['queue'], 'max')
        self.assertEqual(workflow['concurrency']['cancel-in-progress'], 'false')
        group = workflow['concurrency']['group']
        self.assertIn("startsWith(github.ref, 'refs/tags/delivery-accept/')", group)
        self.assertIn('everydayaione-production-acceptance', group)
        self.assertIn('everydayaione-production-delivery', group)
        self.assertNotIn('github.ref_name', group)
        self.assertNotIn('workflow_run', workflow['on'])
        self.assertEqual(workflow['jobs']['rehearsal']['environment'], 'delivery-rehearsal')
        self.assertNotIn('secrets.', json.dumps(workflow['jobs']['rehearsal']))
        for name in ('production', 'acceptance'):
            job = workflow['jobs'][name]
            self.assertEqual(job['environment'], 'production')
            self.assertIn("vars.PRODUCTION_DELIVERY_ENABLED == 'true'", job['if'])
        self.assertEqual(workflow['permissions'], {'contents': 'read', 'actions': 'read'})
        self.assertEqual(workflow['jobs']['acceptance']['permissions']['contents'], 'write')


class ReviewedReleaseRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReleaseCoordinationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def candidate(self, task, filename):
        f = self.fixture
        (task / filename).write_text(filename + '\n')
        f.git('add', filename, cwd=task)
        f.git('commit', '-m', 'test: candidate', cwd=task)
        branch = f.git('branch', '--show-current', cwd=task).stdout.strip()
        f.git('push', '-u', 'origin', branch, cwd=task)
        return f.git('rev-parse', 'HEAD', cwd=task).stdout.strip()

    def reviewed(self, task, sha, **kwargs):
        return self.fixture.run_command(['bash', 'deploy/release.sh', '--deploy-task', sha,
            '--reviewed-sha', sha, '--skip-test'], cwd=task, **kwargs)

    def test_checked_release_then_same_task_revision_preserves_history(self):
        f = self.fixture
        f.marker.write_text(f'commit={f.base}\nmode=main\n')
        task = f.task('a')
        first = self.candidate(task, 'first.txt')
        self.reviewed(task, first)
        self.assertIn('source=codex/task/', f.marker.read_text())
        second = self.candidate(task, 'second.txt')
        self.reviewed(task, second)
        self.assertEqual((f.production / 'written').read_text().strip(), second)
        self.assertTrue((task / 'first.txt').exists())

    def test_unknown_production_version_never_means_empty_server(self):
        f = self.fixture
        task = f.task('a')
        sha = self.candidate(task, 'a.txt')
        result = self.reviewed(task, sha, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('生产版本标记未知', result.stdout)
        self.assertFalse((f.production / 'written').exists())
        self.assertFalse(f.lock.exists())

    def test_other_unaccepted_task_is_not_overwritten_even_if_candidate_contains_it(self):
        f = self.fixture
        f.marker.write_text(f'commit={f.base}\n')
        a = f.task('a')
        first = self.candidate(a, 'a.txt')
        self.reviewed(a, first)
        b = f.task('b')
        branch = f.git('branch', '--show-current', cwd=a).stdout.strip()
        f.git('merge', '--no-edit', branch, cwd=b)
        second = self.candidate(b, 'b.txt')
        result = self.reviewed(b, second, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('其他任务仍待生产验收', result.stdout)
        self.assertEqual((f.production / 'written').read_text().strip(), first)
        self.assertIn('commit=' + first, f.marker.read_text())

    def test_stale_task_cannot_remove_a_deployed_change(self):
        f = self.fixture
        f.marker.write_text(f'commit={f.base}\n')
        a, b = f.task('a'), f.task('b')
        first = self.candidate(a, 'a.txt')
        self.reviewed(a, first)
        second = self.candidate(b, 'b.txt')
        result = self.reviewed(b, second, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('候选未包含当前生产代码', result.stdout)
        self.assertEqual((f.production / 'written').read_text().strip(), first)

    def test_main_move_never_changes_approved_candidate_automatically(self):
        f = self.fixture
        f.marker.write_text(f'commit={f.base}\n')
        task = f.task('a')
        sha = self.candidate(task, 'a.txt')
        (f.repository / 'stable.txt').write_text('stable\n')
        f.git('add', 'stable.txt')
        f.git('commit', '-m', 'test: advance main')
        f.git('push', 'origin', 'main')
        result = self.reviewed(task, sha, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('审批后main已更新', result.stdout)
        self.assertEqual(f.git('rev-parse', 'HEAD', cwd=task).stdout.strip(), sha)
        self.assertFalse((f.production / 'written').exists())

    def test_busy_shared_lock_prevents_any_second_writer(self):
        f = self.fixture
        f.marker.write_text(f'commit={f.base}\n')
        task = f.task('a')
        sha = self.candidate(task, 'a.txt')
        f.lock.mkdir()
        (f.lock / 'owner').write_text('another-writer\n')
        result = self.reviewed(task, sha, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((f.lock / 'owner').read_text(), 'another-writer\n')
        self.assertFalse((f.production / 'written').exists())

    def test_acceptance_then_next_task_integrates_new_main_before_release(self):
        f = self.fixture
        f.marker.write_text(f'commit={f.base}\n')
        a, b = f.task('a'), f.task('b')
        first = self.candidate(a, 'a.txt')
        self.reviewed(a, first)
        f.run_command(['bash', 'deploy/release.sh', '--accept-and-close'], cwd=a)
        f.git('fetch', 'origin', 'main', cwd=b)
        f.git('merge', '--no-edit', 'origin/main', cwd=b)
        second = self.candidate(b, 'b.txt')
        self.reviewed(b, second)
        self.assertTrue((b / 'a.txt').exists())
        self.assertEqual((f.production / 'written').read_text().strip(), second)


if __name__ == '__main__':
    unittest.main()
