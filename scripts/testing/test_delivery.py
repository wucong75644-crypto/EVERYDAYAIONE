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
sys.path.insert(0, str(ROOT / 'scripts/ci'))
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
                              for name in ('frontend', 'backend', 'scope')]}
        self.plan = {'base': 'b' * 40, 'mode': 'scoped', 'fingerprint': 'c' * 64,
                     'frontend': 'full', 'backend': 'full', 'deploy': True}
        self.jobs['jobs'].append({'name': 'required / ' + 'b' * 40 + ' / scoped / ' + 'c' * 64,
                                 'status': 'completed', 'conclusion': 'success'})
        self.plan_patch = patch.object(delivery.check_scope, 'make_plan', return_value=self.plan)
        self.plan_patch.start()
        self.addCleanup(self.plan_patch.stop)

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
            jobs = {'jobs': [self.jobs['jobs'][0], {**self.jobs['jobs'][1], 'conclusion': state}, *self.jobs['jobs'][2:]]}
            with self.subTest(state=state), patch.object(delivery, 'api', side_effect=[self.run, jobs]):
                with self.assertRaises(RuntimeError):
                    delivery.verify_checks(self.repository, '123', self.sha)

    def test_document_only_blocked_by_runner_before_source_or_ssh(self):
        ref = f'refs/tags/delivery-production/{self.sha}/123/task-a/012345abcdef'
        env = {'GITHUB_REF': ref, 'GITHUB_SHA': self.sha,
               'GITHUB_REPOSITORY': self.repository, 'PRODUCTION_DELIVERY_ENABLED': 'true',
               'DELIVERY_APPROVER': 'owner', 'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}
        with patch.dict(os.environ, env), patch.object(delivery, 'verify_actor'), \
             patch.object(delivery, 'verify_environment'), \
             patch.object(delivery, 'verify_checks', return_value={'deploy': False}), \
             patch.object(delivery, 'verify_source') as source:
            with self.assertRaisesRegex(RuntimeError, 'Documentation-only'):
                delivery.make_plan()
            with self.assertRaisesRegex(RuntimeError, 'Documentation-only'):
                delivery.execute()
            source.assert_not_called()

    def test_workflow_green_without_scope_proof_is_not_enough(self):
        for jobs in (self.jobs['jobs'][:-1], self.jobs['jobs'][:2]):
            with patch.object(delivery, 'api', side_effect=[self.run, {'jobs': jobs}]), self.assertRaises(RuntimeError):
                delivery.verify_checks(self.repository, '123', self.sha)

    def test_verification_run_cannot_authorize_production_even_when_green(self):
        run = {**self.run, 'head_branch': 'preflight/verify/' + self.sha + '/docs/012345abcdef'}
        with patch.object(delivery, 'api', return_value=run) as api, self.assertRaisesRegex(RuntimeError, 'Verification-only'):
            delivery.verify_checks(self.repository, '123', self.sha)
        self.assertEqual(api.call_count, 1)

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

    def test_codex_request_policy_has_no_second_approval_and_restricts_tags(self):
        environment={'can_admins_bypass':False,'protection_rules':[{'type':'branch_policy'}],
                     'deployment_branch_policy':{'custom_branch_policies':True,'protected_branches':False}}
        policies={'branch_policies':[{'type':'tag','name':name} for name in
                  ('delivery-production/*/*/*/*','delivery-accept/*/*/*/*','delivery-verification/*/*')]}
        with patch.object(delivery,'api',side_effect=[environment,policies]):
            delivery.verify_environment(self.repository,'production','owner')
        for changes in ({'can_admins_bypass':True},{'protection_rules':[{'type':'required_reviewers'}]},
                        {'deployment_branch_policy':None}):
            with patch.object(delivery,'api',return_value={**environment,**changes}),self.assertRaises(RuntimeError):
                delivery.verify_environment(self.repository,'production','owner')
        with patch.object(delivery,'api',side_effect=[environment,{'branch_policies':[{'type':'tag','name':'*'}]}]),self.assertRaises(RuntimeError):
            delivery.verify_environment(self.repository,'production','owner')

    def test_run_and_rerun_identity_must_match_configured_authorizer(self):
        env={'GITHUB_ACTOR':'owner','GITHUB_TRIGGERING_ACTOR':'owner','GITHUB_RUN_ID':'123','GITHUB_SHA':self.sha}
        run={'actor':{'login':'owner'},'triggering_actor':{'login':'owner'},'head_sha':self.sha,'event':'push'}
        with patch.dict(os.environ,env),patch.object(delivery,'api',return_value=run):
            delivery.verify_actor(self.repository,'owner')
        for key in ('GITHUB_ACTOR','GITHUB_TRIGGERING_ACTOR'):
            with patch.dict(os.environ,{**env,key:'other'}),patch.object(delivery,'api') as api,self.assertRaises(RuntimeError):
                delivery.verify_actor(self.repository,'owner')
            api.assert_not_called()
        with patch.dict(os.environ,env),patch.object(delivery,'api',return_value={**run,'triggering_actor':{'login':'other'}}),self.assertRaises(RuntimeError):
            delivery.verify_actor(self.repository,'owner')

    def test_real_request_requires_matching_human_instruction_before_git_or_api(self):
        for mode in ('production','accept'):
            with patch.object(delivery,'git') as git,patch.object(delivery,'api') as api,self.assertRaises(RuntimeError):
                delivery.request('123',mode,False,'演练')
            git.assert_not_called();api.assert_not_called()

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

    def test_github_has_no_production_execution_or_credentials(self):
        workflow = yaml.load((ROOT / '.github/workflows/delivery.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
        self.assertEqual(set(workflow['jobs']), {'rehearsal'})
        self.assertNotIn('secrets.', json.dumps(workflow))
        self.assertNotIn('environment', workflow['jobs']['rehearsal'])
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        verification = yaml.load((ROOT / '.github/workflows/delivery-verification.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertNotIn('secrets.', json.dumps(verification))
        self.assertNotIn('environment', verification['jobs']['notice'])


class CredentialSerializationTests(unittest.TestCase):
    def test_execute_uses_readable_newline_terminated_private_credentials(self):
        # Generate only a disposable fixture; never inspect configured credentials.
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'fixture'
            real_run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(fixture)], check=True)
            key = fixture.read_text().rstrip()
            fixture.write_text(key)
            unreadable = real_run(['ssh-keygen', '-y', '-P', '', '-f', str(fixture)], capture_output=True)
            self.assertNotEqual(unreadable.returncode, 0)
            self.assertRegex(unreadable.stderr, b'error in libcrypto|invalid format')
            sha = 'a' * 40
            metadata = dict(SERVER_HOST='example.invalid', SERVER_USER='deploy', SERVER_PORT='22',
                REMOTE_APP_DIR='/var/www/everydayai', REMOTE_FRONTEND_DIR='/var/www/everydayai/frontend',
                REMOTE_BACKEND_DIR='/var/www/everydayai/backend', DOMAIN='example.invalid', RUN_MIGRATIONS='false')
            for suffix in ('', '\n', '\n\n'):
                with self.subTest(suffix=repr(suffix)):
                    released = []
                    def local_boundary(command, **kwargs):
                        if command[:3] == ['git', 'worktree', 'add']:
                            (Path(command[-2]) / 'deploy').mkdir(parents=True)
                        elif command[:2] == ['bash', 'deploy/release.sh']:
                            private = Path(kwargs['cwd']).parent
                            identity = private / 'identity'
                            hosts = private / 'known_hosts'
                            self.assertEqual(identity.read_text(), key + '\n')
                            self.assertEqual(hosts.read_text(), 'fixture-host-key\n')
                            for path in (identity, hosts):
                                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                            readable = real_run(['ssh-keygen', '-y', '-P', '', '-f', str(identity)], capture_output=True)
                            self.assertEqual(readable.returncode, 0, 'execute identity must be OpenSSH-readable')
                            self.assertNotIn('DELIVERY_SSH_KEY', kwargs['env'])
                            self.assertEqual(command, ['bash', 'deploy/release.sh', '--deploy-task', sha,
                                                      '--reviewed-sha', sha, '--skip-test'])
                            released.append(True)
                        elif command[:3] != ['git', 'worktree', 'remove']:
                            self.fail('Unexpected subprocess boundary: ' + command[0])
                        return subprocess.CompletedProcess(command, 0)
                    env = dict(GITHUB_REF=f'refs/tags/delivery-production/{sha}/123/task-a/012345abcdef',
                        GITHUB_SHA=sha, GITHUB_REPOSITORY='example/repository', PRODUCTION_DELIVERY_ENABLED='true',
                        GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', DELIVERY_APPROVER='fixture',
                        DELIVERY_CONFIG_JSON=json.dumps(metadata), DELIVERY_SSH_KEY=key + suffix,
                        DELIVERY_KNOWN_HOSTS='fixture-host-key' + suffix)
                    with patch.dict(os.environ, env), patch.dict(sys.modules, {'verify_delivery': type('Probe', (), {'verify_connection': staticmethod(lambda: None)})}), \
                         patch.object(delivery, 'verify_actor'), patch.object(delivery, 'verify_environment'), \
                         patch.object(delivery, 'verify_checks', return_value={'deploy': True}), patch.object(delivery, 'verify_source'), \
                         patch.object(delivery.subprocess, 'run', side_effect=local_boundary):
                        delivery.execute()
                    self.assertEqual(released, [True])


class PrivateReleaseOutputTests(unittest.TestCase):
    def test_execute_never_publishes_child_output_on_success_or_failure(self):
        import contextlib
        real_run = subprocess.run
        sha = 'a' * 40
        metadata = dict(SERVER_HOST='example.invalid', SERVER_USER='deploy', SERVER_PORT='22',
            REMOTE_APP_DIR='/var/www/everydayai', REMOTE_FRONTEND_DIR='/var/www/everydayai/frontend',
            REMOTE_BACKEND_DIR='/var/www/everydayai/backend', DOMAIN='example.invalid', RUN_MIGRATIONS='false')
        payload = ("https://example.invalid/?token=QUERY_SECRET&unknown=OTHER_SECRET\n"
                   "Authorization: Bearer HEADER_SECRET\nCookie: session=COOKIE_SECRET\n"
                   '{"message":"BUSINESS_SECRET"}\n' + '-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5 + '\nKEY_SECRET\n'
                   + '-' * 5 + 'END PRIVATE KEY' + '-' * 5 + '\n::warning::WORKFLOW_SECRET\n'
                   "RELEASE_RESULT status=success commit=BAD_SECRET source=BAD_SECRET\n")
        for mode in ('production', 'accept'):
            for exit_code in (0, 7):
                with self.subTest(mode=mode, exit=exit_code):
                    records = (f'RELEASE_RESULT status=success commit={sha} source=codex/task/task-a '
                               'mode=task status_after=DEPLOYED_PENDING_ACCEPTANCE acceptance_candidate=true'
                               if not exit_code else
                               f'RELEASE_RESULT status=failed commit={sha} executor_state=unconfirmed log=PATH_SECRET\n'
                               'RELEASE_LOCK_RESULT status=retained reason=executor_unconfirmed '
                               'executor_state=unconfirmed operation_exit=7 path=PATH_SECRET')
                    if mode == 'accept' and not exit_code:
                        records = f'TASK_ACCEPTANCE_RESULT status=success deployed_commit={sha} stable_main={sha} deployment=skipped'
                    captured_files = []
                    def boundary(command, **kwargs):
                        if command[:3] == ['git', 'worktree', 'add']:
                            task = Path(command[-2]); (task / 'deploy').mkdir(parents=True)
                            script = task / 'deploy/release.sh'
                            script.write_text("cat <<'FIXTURE_STDOUT'\n" + payload + records +
                                              "\nFIXTURE_STDOUT\ncat >&2 <<'FIXTURE_STDERR'\n" + payload + records +
                                              f"\nFIXTURE_STDERR\nexit {exit_code}\n")
                            return subprocess.CompletedProcess(command, 0)
                        if command[:2] == ['bash', 'deploy/release.sh']:
                            output = kwargs['stdout']
                            self.assertEqual(os.fstat(output.fileno()).st_mode & 0o777, 0o600)
                            self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
                            captured_files.append(output)
                            return real_run(command, **kwargs)
                        if command[:3] == ['git', 'worktree', 'remove']:
                            return subprocess.CompletedProcess(command, 0)
                        self.fail('Unexpected subprocess')
                    env = dict(GITHUB_REF=f'refs/tags/delivery-{mode}/{sha}/123/task-a/012345abcdef',
                        GITHUB_SHA=sha, GITHUB_REPOSITORY='example/repository', PRODUCTION_DELIVERY_ENABLED='true',
                        GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted', DELIVERY_APPROVER='fixture',
                        DELIVERY_CONFIG_JSON=json.dumps(metadata), DELIVERY_SSH_KEY='fixture-key',
                        DELIVERY_KNOWN_HOSTS='fixture-hosts', GH_TOKEN='fixture-token')
                    public_out, public_err = io.StringIO(), io.StringIO()
                    with patch.dict(os.environ, env), patch.dict(sys.modules, {'verify_delivery': type('Probe', (), {'verify_connection': staticmethod(lambda: None)})}), \
                         patch.object(delivery, 'verify_actor'), patch.object(delivery, 'verify_environment'), \
                         patch.object(delivery, 'verify_checks', return_value={'deploy': True}), patch.object(delivery, 'verify_source'), \
                         patch.object(delivery, 'git'), patch.object(delivery.subprocess, 'run', side_effect=boundary), \
                         contextlib.redirect_stdout(public_out), contextlib.redirect_stderr(public_err):
                        if exit_code:
                            with self.assertRaisesRegex(RuntimeError, 'exit=7.*lock=retained') as error:
                                delivery.execute()
                            self.assertNotIn('SECRET', str(error.exception))
                        else:
                            delivery.execute()
                    public = public_out.getvalue() + public_err.getvalue()
                    self.assertNotIn('SECRET', public)
                    for forbidden in ('https://', 'Authorization', 'Cookie', 'PRIVATE KEY', '::warning::', 'message', 'log=', 'path='):
                        self.assertNotIn(forbidden, public)
                    self.assertIn('status=' + ('failed' if exit_code else 'success'), public)
                    self.assertIn('DELIVERY_EXECUTION_RESULT exit=' + str(exit_code), public)
                    self.assertTrue(captured_files)
                    self.assertTrue(all(f.closed for f in captured_files))


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
        f.marker.unlink()
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
