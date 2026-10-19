import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('diagnostics', ROOT/'scripts/ci/diagnostics_gateway.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class DiagnosticsTests(unittest.TestCase):
    def test_secrets_and_free_text_never_cross_boundary(self):
        sentinel = 'SENSITIVE_SENTINEL_12345'
        messages = [
            f'GET https://provider.invalid/api?token={sentinel} HTTP/1.1 500',
            f'Authorization: Bearer {sentinel}\npassword={sentinel}',
            f'OperationalError: postgres://user:{sentinel}@db/private customer={sentinel}',
            f'Traceback (most recent call last):\n  File "/var/www/everydayai/backend/app.py", line 42, in handler\n    api_key="{sentinel}"\nValueError: {sentinel}',
            '-----BEGIN '+'PRIVATE KEY-----\n'+sentinel+'\n-----END '+'PRIVATE KEY-----',
        ]
        for message in messages:
            with self.subTest(message=message):
                encoded = json.dumps(diagnostics.project_message(message))
                self.assertNotIn(sentinel, encoded)
                self.assertNotIn('postgres://', encoded)
                self.assertNotIn('provider.invalid', encoded)
                self.assertNotIn('Authorization', encoded)
        result = diagnostics.project_message(messages[3])
        self.assertEqual(result['frames'], [{'file': 'backend/app.py', 'line': 42}])
        self.assertEqual(result['exception_types'], ['ValueError'])

    def test_non_string_journal_and_outside_frames(self):
        for value in (None, [1, 2], {'MESSAGE': 'secret'}):
            self.assertEqual(diagnostics.project_message(value), {'categories': ['unclassified']})
        self.assertNotIn('frames', diagnostics.project_message('File "/root/private.py", line 1'))
        self.assertNotIn('frames', diagnostics.project_message('File "/var/www/everydayai/backend/../../private.py", line 1'))

    def test_all_arbitrary_commands_denied_before_collection(self):
        for command in ('', 'id', 'cat /etc/passwd', 'systemctl restart everydayai-backend',
                        'everydayai-diagnostics-v1; id', 'everydayai-diagnostics-v1 --since=all',
                        'everydayai-diagnostics-v1\n'):
            with self.subTest(command=command), patch.dict(diagnostics.os.environ, {'SSH_ORIGINAL_COMMAND': command}), patch.object(diagnostics, 'collect') as collect:
                with self.assertRaises(ValueError):
                    diagnostics.main()
                collect.assert_not_called()

    def test_journal_query_is_fixed_bounded_and_projected(self):
        raw = json.dumps({'__REALTIME_TIMESTAMP': '1792375000000000', 'PRIORITY': '3',
                          'MESSAGE': 'RuntimeError: SECRET_SENTINEL'})+'\n'
        with patch.object(diagnostics, 'bounded_command', return_value=(0, raw.encode(), False)) as run:
            result = diagnostics.journal('everydayai-backend')
        args = run.call_args.args[0]
        self.assertIn('--since=-15min', args)
        self.assertIn('--lines=100', args)
        self.assertNotIn('SECRET_SENTINEL', json.dumps(result))
        self.assertEqual(result['records'][0]['exception_types'], ['RuntimeError'])
        self.assertEqual(result['records'][0]['priority'], 3)

    def test_partial_journal_and_failed_query_are_explicit(self):
        with patch.object(diagnostics, 'bounded_command', return_value=(0, b'{bad', True)):
            self.assertEqual(diagnostics.journal('nginx'), {'query': 'ok', 'truncated': True, 'records': []})
        with patch.object(diagnostics, 'bounded_command', return_value=(1, b'SECRET', False)):
            self.assertEqual(diagnostics.journal('nginx'), {'query': 'failed', 'records': []})

    def test_status_only_includes_allowlisted_fields(self):
        raw = b'ActiveState=active\nSubState=running\nMainPID=123\nNRestarts=2\nExecMainStatus=0\nEnvironment=SECRET\n'
        with patch.object(diagnostics, 'bounded_command', return_value=(0, raw, False)):
            result = diagnostics.service_status('nginx')
        self.assertEqual(result['MainPID'], 123)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_timeout_does_not_disclose_raw_errors(self):
        with patch.object(diagnostics, 'service_status', side_effect=subprocess.TimeoutExpired('SECRET', 10)):
            result = diagnostics.collect()
        self.assertTrue(all(v == {'query': 'failed'} for v in result['services'].values()))
        self.assertFalse(result['raw_messages_included'])
        self.assertNotIn('SECRET', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
