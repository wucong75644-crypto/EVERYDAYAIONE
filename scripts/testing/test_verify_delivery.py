"""Read-only diagnostics retain failure semantics without publishing raw SSH output."""
import contextlib
import hashlib
import io
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
import verify_delivery as probe


class ProbeTests(unittest.TestCase):
    def report(self):
        return {'protocol': 1, 'uid': 0,
                'paths_exist': {'/var/www/everydayai' + suffix: True for suffix in
                                ('', '/frontend', '/backend', '/deploy', '/backend/venv/bin/python',
                                 '/.production-database-identity.json')},
                'services_active': {name: True for name in ('everydayai-backend', 'everydayai-sync',
                    'everydayai-wecom', 'everydayai-conversation-actor', 'nginx')},
                'production_commit': 'a' * 40, 'policy_source': 'b' * 40,
                'policy_sha256': 'c' * 64,
                'gateway_sha256': hashlib.sha256((ROOT / 'scripts/ci/ssh_gateway.py').read_bytes()).hexdigest(),
                'policy_scripts_sha256': hashlib.sha256(json.dumps(probe.build()['scripts'], sort_keys=True).encode()).hexdigest(),
                'lock_present': False, 'database_accessed': False, 'deployed': False}

    def run_probe(self, report, code=0):
        policy = probe.build()
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary'
            env = {'GITHUB_REPOSITORY': 'example/repo', 'DELIVERY_APPROVER': 'owner',
                   'DELIVERY_CONFIG_JSON': '{}', 'DELIVERY_SSH_KEY': 'synthetic',
                   'DELIVERY_KNOWN_HOSTS': 'synthetic', 'GITHUB_STEP_SUMMARY': str(summary)}
            results = [subprocess.CompletedProcess([], code, json.dumps(report), 'private stderr'),
                       subprocess.CompletedProcess([], 1, '', 'SSH_GATEWAY_DENIED'),
                       subprocess.CompletedProcess([], 0, '', '')]
            output = io.StringIO()
            error = None
            with patch.dict(os.environ, env), patch.object(probe, 'build', return_value=policy), patch.object(probe, 'verify_actor'), \
                 patch.object(probe, 'verify_environment'), patch.object(probe, 'config_text',
                 return_value='SERVER_PORT=22\nSERVER_USER=synthetic\nSERVER_HOST=example.invalid'), \
                 patch.object(probe.subprocess, 'run', side_effect=results), contextlib.redirect_stdout(output):
                try:
                    probe.verify_connection()
                except RuntimeError as exc:
                    error = str(exc)
            return error, output.getvalue(), summary.read_text() if summary.exists() else ''

    def test_healthy_probe_succeeds(self):
        error, output, _ = self.run_probe(self.report())
        self.assertIsNone(error)
        self.assertTrue(json.loads(output)['arbitrary_command_denied'])

    def test_retained_lock_or_inactive_service_remains_failed_but_visible(self):
        for change in ('lock', 'service', 'marker'):
            report = self.report()
            if change == 'lock':
                report['lock_present'] = True
            elif change == 'service':
                report['services_active']['everydayai-backend'] = False
            else:
                report['production_commit'] = ''
            error, output, summary = self.run_probe(report, code=1)
            self.assertIn('unhealthy release state', error)
            self.assertEqual(json.loads(output)['lock_present'], report['lock_present'])
            self.assertIn('services_active', summary)
            self.assertNotIn('private stderr', output + summary)

    def test_untrusted_fields_never_published(self):
        report = self.report()
        report['secret'] = 'PRIVATE_SENTINEL'
        self.assertNotIn('secret', probe.safe_probe_report(json.dumps(report)))
        for key, value in [('production_commit', 'PRIVATE_SENTINEL'),
                           ('lock_present', 'PRIVATE_SENTINEL'), ('paths_exist', {'PRIVATE_SENTINEL': True})]:
            invalid = {**report, key: value}
            error, output, summary = self.run_probe(invalid, code=1)
            self.assertIn('raw output withheld', error)
            self.assertEqual(output + summary, '')

    def test_identity_mismatch_not_published(self):
        report = {**self.report(), 'gateway_sha256': 'f' * 64}
        error, output, summary = self.run_probe(report, code=1)
        self.assertIn('identity mismatch', error)
        self.assertEqual(output + summary, '')


if __name__ == '__main__':
    unittest.main()
