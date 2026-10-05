"""Execute the actual deployment orchestration with all IO replaced by local traces."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2]


class DeployPreflightTests(unittest.TestCase):
    def run_main(self, *, failure='', options=()):
        source = (SOURCE / 'deploy/deploy.sh').read_text()
        # Keep the real functions, phase tracking and main; stub only IO.
        source = source.rsplit('main "$@"', 1)[0]
        stubs = '''
check_config() { RUN_MIGRATIONS=false; DOMAIN=example.invalid; SERVER_USER=test; SERVER_HOST=test; SERVER_PORT=22; }
check_dependencies() { :; }
require_controlled_release() { :; }
validate_release_python() { :; }
test_ssh_connection() { :; }
remote_exec() { cat >/dev/null; }
'''
        for name in ['build_frontend', 'build_backend', 'setup_server', 'sync_frontend',
                     'deploy_frontend', 'prepare_scheduled_task_cutover', 'prepare_skill_personal_mount',
                     'sync_backend', 'apply_migrations', 'deploy_backend', 'show_status']:
            stubs += (f'{name}() {{ printf "%s\\n" {name} >> "$TRACE"; '
                      f'if [[ "$FAILURE" == {name} ]]; then exit 42; fi; }}\n')
        with tempfile.TemporaryDirectory() as root:
            trace = Path(root) / 'trace'
            status = Path(root) / 'status'
            process = subprocess.run(['bash', '-s', '--', *options], input=source + stubs + 'main "$@"\n',
                                     cwd=SOURCE, env={**os.environ, 'TRACE': str(trace), 'FAILURE': failure,
                                     'EVERYDAYAI_DEPLOY_STATUS_FILE': str(status)}, text=True,
                                     capture_output=True, timeout=10)
            return process.returncode, trace.read_text().splitlines(), status.read_text().strip()

    def test_backend_test_failure_precedes_all_production_writes(self):
        code, trace, status = self.run_main(failure='build_backend')
        self.assertEqual(code, 42)
        self.assertEqual(trace, ['build_frontend', 'build_backend'])
        self.assertEqual(status, 'preflight_failed')

    def test_all_builds_pass_before_first_write(self):
        code, trace, status = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(trace[:3], ['build_frontend', 'build_backend', 'sync_frontend'])
        self.assertEqual(status, 'completed')

    def test_remote_stage_failure_remains_uncertain(self):
        code, trace, status = self.run_main(failure='sync_backend')
        self.assertEqual(code, 42)
        self.assertIn('deploy_frontend', trace)
        self.assertEqual(status, 'uncertain')

    def test_partial_deployment_builds_only_selected_component(self):
        for option, omitted in [('--frontend-only', 'build_backend'), ('--backend-only', 'build_frontend')]:
            with self.subTest(option=option):
                code, trace, status = self.run_main(options=[option])
                self.assertEqual(code, 0)
                self.assertNotIn(omitted, trace)
                self.assertEqual(status, 'completed')


if __name__ == '__main__':
    unittest.main()
