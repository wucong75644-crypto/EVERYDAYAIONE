#!/usr/bin/env python3
"""Local Git + fake binary tests. No network, model, database or production access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2]
HELPER = SOURCE / 'scripts/aoci-task.py'
spec = importlib.util.spec_from_file_location('aoci_task', HELPER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AociTaskTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'root'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.root / '.gitignore').write_text('.codex/\n')
        (self.root / 'product.txt').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.binary = self.base / 'fake-aoci'
        self.binary.write_text('''#!/usr/bin/env python3
import json, os, sys
if '--version' in sys.argv:
    print('aoci version 0.1.0-rc17 (test)')
elif 'verify' in sys.argv:
    print(json.dumps({'structure_valid': True,
                     'governance_aligned': os.environ.get('FAKE_STALE') != '1',
                     'read_only_candidate': os.environ.get('FAKE_READ_ONLY', '1') == '1'}))
elif 'check' in sys.argv:
    sys.exit(int(os.environ.get('FAKE_CHECK_EXIT', '0')))
else:
    sys.exit(99)
''')
        self.binary.chmod(0o755)
        self.env = {**os.environ, 'AOCI_BINARY': str(self.binary)}

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, root=None):
        return subprocess.run(['git', '-C', str(root or self.root), *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def assets(self):
        for relative in module.FORMAL:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}\n' if relative.endswith('.json') else 'fixture\n')
        (self.root / '.aoci/.gitignore').write_text('*\n!.gitignore\n!config.json\n!baseline.json\n')

    def invoke(self, command, root=None, **env):
        result = subprocess.run(['python3', str(HELPER), command, '--repo', str(root or self.root)],
                                env={**self.env, **env}, capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout or result.stderr)

    def test_disabled_projects_need_no_binary(self):
        self.env['AOCI_BINARY'] = '/no/such/tool'
        code, report = self.invoke('verify')
        self.assertEqual((code, report['status']), (0, 'not_enabled'))

    def test_independent_worktrees_and_local_config_ignored(self):
        self.assets()
        self.git('add', '.')
        self.git('commit', '-qm', 'aoci-assets')
        second = self.base / 'second'
        self.git('worktree', 'add', '-qb', 'codex/task/second', str(second))
        for root in (self.root, second):
            self.assertEqual(self.invoke('prepare', root=root)[0], 0)
            config = module.tomllib.loads((root / '.codex/config.toml').read_text())
            self.assertEqual(config['mcp_servers']['aoci']['args'], ['--repo', str(root), 'mcp'])
            self.assertEqual(self.git('status', '--porcelain', root=root), '')
        before = (second / '.codex/config.toml').read_bytes()
        self.invoke('prepare', root=second)
        self.assertEqual((second / '.codex/config.toml').read_bytes(), before)

    def test_unrelated_config_preserved(self):
        self.assets()
        config = self.root / '.codex/config.toml'
        config.parent.mkdir()
        original = 'model = "custom"\n[mcp_servers.other]\ncommand = "/other"\n'
        config.write_text(original)
        self.assertEqual(self.invoke('prepare')[0], 0)
        self.assertTrue(config.read_text().startswith(original))

    def test_existing_wrong_binding_rejected_without_writes(self):
        self.assets()
        config = self.root / '.codex/config.toml'
        config.parent.mkdir()
        original = '[mcp_servers.aoci]\ncommand = "/other"\nargs = ["--repo", "/wrong", "mcp"]\n'
        config.write_text(original)
        self.assertEqual(self.invoke('prepare')[0], 1)
        self.assertEqual(config.read_text(), original)

    def test_symlink_config_rejected_without_touching_target(self):
        self.assets()
        target = self.base / 'user-config'
        target.write_text('model = "custom"\n')
        (self.root / '.codex').mkdir()
        (self.root / '.codex/config.toml').symlink_to(target)
        self.assertEqual(self.invoke('prepare')[0], 1)
        self.assertEqual(target.read_text(), 'model = "custom"\n')

    def test_managed_config_preserves_user_options_and_disabled_state(self):
        self.assets()
        self.assertEqual(self.invoke('prepare')[0], 0)
        config = self.root / '.codex/config.toml'
        with config.open('a') as f:
            f.write('startup_timeout_sec = 40\n')
        self.assertEqual(self.invoke('prepare')[0], 0)
        self.assertIn('startup_timeout_sec = 40', config.read_text())
        with config.open('a') as f:
            f.write('enabled = false\n')
        before = config.read_bytes()
        self.assertEqual(self.invoke('prepare')[0], 1)
        self.assertEqual(config.read_bytes(), before)

    def test_aligned_and_stale_gate(self):
        self.assets()
        self.assertEqual(self.invoke('verify')[0], 0)
        self.assertEqual(self.invoke('verify', FAKE_STALE='1')[0], 1)
        self.assertEqual(self.invoke('verify', FAKE_CHECK_EXIT='1')[0], 1)

    def test_read_only_candidate_is_not_governance_drift(self):
        self.assets()
        for value in ('0', '1'):
            code, report = self.invoke('verify', FAKE_READ_ONLY=value)
            self.assertEqual((code, report['status']), (0, 'aligned'))
            code, report = self.invoke('session', FAKE_READ_ONLY=value)
            self.assertEqual((code, report['status']), (0, 'aligned'))
            self.assertEqual(self.invoke('verify', FAKE_READ_ONLY=value,
                                         FAKE_STALE='1')[0], 1)

    def test_missing_assets_cannot_disable_gate(self):
        self.assets()
        self.git('add', '.')
        self.git('commit', '-qm', 'aoci-assets')
        for relative in module.FORMAL:
            (self.root / relative).unlink()
        self.assertEqual(self.invoke('verify')[0], 1)

    def test_wrong_version_rejected(self):
        self.assets()
        self.binary.write_text('#!/bin/sh\nprintf "aoci version 0.2.0\\n"\n')
        self.assertEqual(self.invoke('prepare')[0], 1)

    def test_pending_stable_update_preserves_dirty_task(self):
        old_head = self.git('rev-parse', 'HEAD')
        second = self.base / 'stable'
        self.git('worktree', 'add', '-qb', 'stable', str(second))
        (second / 'new.txt').write_text('accepted\n')
        self.git('add', '.', root=second)
        self.git('commit', '-qm', 'accepted', root=second)
        stable = self.git('rev-parse', 'HEAD', root=second)
        self.git('config', 'extensions.worktreeConfig', 'true')
        self.git('config', '--worktree', 'codex.aociStableCommit', stable)
        (self.root / 'product.txt').write_text('staged\n')
        self.git('add', 'product.txt')
        (self.root / 'product.txt').write_text('unstaged\n')
        (self.root / 'untracked.txt').write_text('user\n')
        before = (self.git('write-tree'), self.git('diff'), self.git('status', '--porcelain'))
        code, report = self.invoke('session')
        self.assertEqual((code, report['status']), (2, 'stable_update_pending'))
        self.assertTrue(report['dirty'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), old_head)
        self.assertEqual(before, (self.git('write-tree'), self.git('diff'), self.git('status', '--porcelain')))

    def test_start_and_sync_use_inherited_assets_without_overwriting_task(self):
        self.assets()
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('task-worktree.sh', 'aoci-task.py'):
            (scripts / name).write_bytes((SOURCE / 'scripts' / name).read_bytes())
            (scripts / name).chmod(0o755)
        (self.root / 'deploy').mkdir()
        (self.root / 'deploy/fixture.txt').write_text('tracked directory\n')
        (self.root / 'deploy/config.env').write_text('fixture=local\n')
        with (self.root / '.gitignore').open('a') as f:
            f.write('deploy/config.env\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'integration')
        self.git('branch', '-M', 'main')
        remote = self.base / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', '-q', 'origin', 'main')
        task = self.base / 'new-task'
        command = [str(scripts / 'task-worktree.sh')]
        created = subprocess.run(command + ['start', 'new', '--path', str(task)], cwd=self.root,
                                 env=self.env, capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        config = module.tomllib.loads((task / '.codex/config.toml').read_text())
        self.assertEqual(config['mcp_servers']['aoci']['args'], ['--repo', str(task), 'mcp'])
        baseline_before = (task / '.aoci/baseline.json').read_bytes()
        head_before = self.git('rev-parse', 'HEAD', root=task)
        (task / 'product.txt').write_text('in progress\n')
        (self.root / 'accepted.txt').write_text('stable\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'accepted')
        self.git('push', '-q', 'origin', 'main')
        stable = self.git('rev-parse', 'HEAD')
        subprocess.run(command + ['sync-stable-base', '--commit', stable], cwd=self.root,
                       env=self.env, check=True, capture_output=True)
        self.assertEqual(self.git('config', '--worktree', '--get', 'codex.aociStableCommit', root=task), stable)
        self.assertEqual(self.git('rev-parse', 'HEAD', root=task), head_before)
        self.assertEqual((task / 'product.txt').read_text(), 'in progress\n')
        self.assertEqual((task / '.aoci/baseline.json').read_bytes(), baseline_before)
        self.assertEqual(self.invoke('session', root=task)[1]['status'], 'stable_update_pending')

    def test_unresolvable_stable_marker_fails(self):
        self.git('config', 'codex.aociStableCommit', '0' * 40)
        self.assertEqual(self.invoke('session')[0], 1)

    def test_stale_session_requires_maintenance(self):
        self.assets()
        code, report = self.invoke('session', FAKE_STALE='1')
        self.assertEqual((code, report['status']), (2, 'maintenance_required'))


if __name__ == '__main__':
    unittest.main()
