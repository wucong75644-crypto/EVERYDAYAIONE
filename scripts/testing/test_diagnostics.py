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
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'backend').mkdir()
            (root/'backend/app.py').write_text('# code')
            with patch.object(diagnostics, 'APP_ROOT', root):
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
        raw = json.dumps({'__REALTIME_TIMESTAMP': str(int(diagnostics.datetime.datetime.now(diagnostics.datetime.timezone.utc).timestamp()*1000000)), 'PRIORITY': '3',
                          'MESSAGE': 'RuntimeError: SECRET_SENTINEL'})+'\n'
        with patch.object(diagnostics, 'bounded_command', return_value=(0, raw.encode(), False)) as run:
            result = diagnostics.journal('everydayai-backend')
        args = run.call_args.args[0]
        self.assertIn('--since=-15min', args)
        self.assertIn('--no-tail', args)
        self.assertEqual(run.call_args.kwargs['line_limit'], 5001)
        self.assertIn('--reverse', args)
        self.assertNotIn('SECRET_SENTINEL', json.dumps(result))
        self.assertEqual(result['records'][0]['exception_types'], ['RuntimeError'])
        self.assertEqual(result['records'][0]['priority'], 3)

    def test_partial_journal_and_failed_query_are_explicit(self):
        with patch.object(diagnostics, 'bounded_command', return_value=(0, b'{bad\n', True)):
            result = diagnostics.journal('nginx')
            self.assertTrue(result['scan_truncated'])
            self.assertEqual(result['parse_errors'], 1)
            self.assertEqual(result['records'], [])
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


    def test_loguru_source_level_and_chinese_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'backend/services').mkdir(parents=True)
            (root/'backend/services/example.py').write_text('# code')
            with patch.object(diagnostics, 'APP_ROOT', root):
                result = diagnostics.project_message('2026-10-19 10:00:00.000 | WARNING | services.example:request:42 - 请求失败 重试 限流 password=SECRET')
                self.assertEqual(result['source'], {'file':'backend/services/example.py','line':42})
                self.assertEqual(result['level'], 'WARNING')
                self.assertIn('rate_limit', result['categories'])
                self.assertIn('retry', result['categories'])
                self.assertNotIn('SECRET', json.dumps(result))
                self.assertNotIn('source', diagnostics.project_message('2026-10-19 10:00:00 | ERROR | services.SECRET:request:1 - SECRET'))
        self.assertEqual(diagnostics.project_message('2026-10-19 10:00:00 | DEBUG | core.redis:release:1 - ordinary')['categories'], ['routine'])

    def test_error_priority_prevents_debug_flood_hiding_errors(self):
        records = [{'time':str(5000-i), 'level':'DEBUG', 'categories':['routine']} for i in range(200)]
        records.append({'time':'1', 'level':'ERROR', 'categories':['error'], 'source':{'file':'backend/main.py','line':42}})
        report = diagnostics.summarize(records, scanned=201, truncated=False)
        self.assertEqual(len(report['records']), 100)
        self.assertEqual(report['important_records_omitted'], 0)
        self.assertEqual(report['category_counts']['error'], 1)
        self.assertTrue(any(r['level']=='ERROR' for r in report['records']))

    def test_nginx_safe_and_combined_no_sensitive_fields(self):
        lines = [
            '192.0.2.1 [19/Oct/2026:10:00:00 +0800] "POST /api/chat/SECRET?token=SECRET HTTP/1.1" 502 100 6.123',
            '192.0.2.1 - SECRET [19/Oct/2026:10:00:00 +0800] "GET /api/files/SECRET HTTP/1.1" 404 42 "https://SECRET" "SECRET"',
        ]
        for line in lines:
            result = diagnostics.nginx_line(line, 'access')
            self.assertIsNotNone(result)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertNotIn('192.0.2.1', json.dumps(result))
        self.assertEqual(diagnostics.nginx_line(lines[0], 'access')['duration_ms'], 6123)
        result = diagnostics.nginx_line('2026/10/19 10:00:00 [error] 123#1: *1 upstream timed out while reading response header from upstream, client: SECRET, request: "GET /SECRET?token=SECRET HTTP/1.1"', 'error')
        self.assertIn('upstream_timeout', result['categories'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_nginx_paths_cannot_be_user_selected(self):
        for path in ('/etc/passwd', '/var/log/nginx/../../etc/shadow', '/var/log/nginx/custom.log'):
            with self.assertRaises(ValueError):
                diagnostics.nginx_file(path)

    def test_nginx_missing_unsafe_and_parse_failure_reported(self):
        with patch.object(diagnostics.os, 'open', side_effect=FileNotFoundError):
            self.assertEqual(diagnostics.nginx_file('/var/log/nginx/access.log')['query'], 'absent')
        with patch.object(diagnostics.os, 'open', side_effect=OSError):
            self.assertEqual(diagnostics.nginx_file('/var/log/nginx/access.log')['query'], 'failed')
        self.assertIsNone(diagnostics.nginx_line('SECRET invalid format', 'access'))

    def test_upload_summary_does_not_treat_failed_zero_as_error(self):
        base = '2026-10-19 10:00:00 | INFO | services.adapters.kie.client:upload:591 - KIE_SHADOW_UPLOAD_SUMMARY | task_id=SECRET | attempted=1 | succeeded=1 | failed=0 | duration_ms=120'
        result = diagnostics.project_message(base)
        self.assertEqual(result['categories'], ['routine'])
        self.assertEqual(result['counts']['failed'], 0)
        self.assertNotIn('SECRET', json.dumps(result))
        result = diagnostics.project_message(base.replace('failed=0', 'failed=1'))
        self.assertEqual(result['categories'], ['shadow_upload_failure'])

    def test_errors_survive_warning_flood(self):
        records = [{'time':'2026-10-19T10:00:00+00:00', 'level':'WARNING', 'categories':['warning']} for _ in range(200)]
        records.append({'time':'2026-10-19T09:59:00+00:00','level':'ERROR','categories':['error']})
        result = diagnostics.summarize(records, scanned=201, truncated=False)
        self.assertTrue(any(r.get('level')=='ERROR' for r in result['records']))
        self.assertEqual(result['important_records_omitted'], 101)

    def test_real_file_read_parsing_failure_and_symlink_denial(self):
        import tempfile
        from types import SimpleNamespace
        original_open, original_fstat = diagnostics.os.open, diagnostics.os.fstat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root/'var/log/nginx'
            logs.mkdir(parents=True)
            target = logs/'access.log'
            target.write_text('malformed SECRET line\n')
            def opened(path, flags, **kwargs):
                return original_open(directory if path == '/' else path, flags, **kwargs)
            def stated(fd):
                result = original_fstat(fd)
                return SimpleNamespace(st_mode=result.st_mode,st_uid=0,st_gid=0,st_size=result.st_size)
            with patch.object(diagnostics.os,'open',side_effect=opened), patch.object(diagnostics.os,'fstat',side_effect=stated):
                result = diagnostics.nginx_file('/var/log/nginx/access.log')
                self.assertEqual(result['parse_errors'], 1)
                self.assertFalse(result['parsing_complete'])
                self.assertNotIn('SECRET', json.dumps(result))
                target.unlink()
                target.symlink_to(logs/'secret')
                self.assertEqual(diagnostics.nginx_file('/var/log/nginx/access.log')['query'], 'failed')

    def test_uvicorn_access_classified_without_path_details(self):
        result = diagnostics.project_message('INFO:     192.0.2.1:12345 - "GET /api/chat/SECRET?token=SECRET HTTP/1.1" 502 Bad Gateway')
        self.assertEqual(result['http_status'], [502])
        self.assertEqual(result['categories'], ['http_server_error'])
        self.assertEqual(result['level'], 'INFO')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('192.0.2.1', json.dumps(result))

    def test_unverified_traceback_file_is_never_output(self):
        result = diagnostics.project_message('File "/var/www/everydayai/backend/SECRET_SENTINEL.py", line 42')
        self.assertNotIn('SECRET_SENTINEL', json.dumps(result))
        self.assertNotIn('frames', result)

    def test_invalid_request_still_has_status_without_raw_payload(self):
        result = diagnostics.nginx_line('192.0.2.1 - - [19/Oct/2026:10:00:00 +0800] "-" 400 0 "SECRET" "SECRET"', 'access')
        self.assertEqual(result['http_status'], [400])
        self.assertEqual(result['method'], 'UNKNOWN')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_parse_failure_is_not_complete_collection(self):
        import sys
        sys.path.insert(0, str(ROOT/'scripts/ci'))
        import query_diagnostics
        services = {unit:{'status':{'query':'ok'}, 'journal':{'query':'ok','parsing_complete':True}} for unit in diagnostics.UNITS}
        files = {p:{'query':'ok','parsing_complete':True, 'kind':'access' if 'access' in p else 'error'} for p in diagnostics.NGINX_FILES}
        report = {'services':services, 'nginx_files':files}
        self.assertTrue(query_diagnostics.collection_complete(report))
        files['/var/log/nginx/access.log']['parsing_complete'] = False
        self.assertFalse(query_diagnostics.collection_complete(report))
        files['/var/log/nginx/access.log']['parsing_complete'] = True
        services['nginx']['journal']['parsing_complete'] = False
        self.assertFalse(query_diagnostics.collection_complete(report))

    def test_byte_budget_terminates_writer_and_timeout(self):
        import sys
        code, data, truncated = diagnostics.bounded_command([sys.executable, '-c', 'import sys;sys.stdout.write("x"*1000000)'], limit=128)
        self.assertTrue(truncated)
        self.assertEqual(len(data), 128)


if __name__ == '__main__':
    unittest.main()
