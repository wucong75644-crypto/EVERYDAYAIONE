"""Exercise scoped commit/push using temporary repositories, never GitHub."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TaskPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        self.main = self.base / "main"
        self.task = self.base / "task"
        self.run_git("init", "--bare", str(self.remote))
        self.run_git("init", "-b", "main", str(self.main))
        self.run_git("config", "user.name", "test", cwd=self.main)
        self.run_git("config", "user.email", "test@example.invalid", cwd=self.main)
        (self.main / "scripts").mkdir()
        for source in ["git-push.sh", "scripts/task-git-push.py"]:
            (self.main / source).write_text((ROOT / source).read_text())
        (self.main / "selected.txt").write_text("original\n")
        self.run_git("add", ".", cwd=self.main)
        self.run_git("commit", "-m", "fixture", cwd=self.main)
        self.run_git("remote", "add", "origin", str(self.remote), cwd=self.main)
        self.run_git("push", "origin", "main", cwd=self.main)
        self.run_git("worktree", "add", "-b", "codex/task/test", str(self.task), cwd=self.main)
        (self.task / "selected.txt").write_text("changed\n")

    def run_git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def push(self, *args, cwd=None):
        return subprocess.run(["bash", "git-push.sh", *args], cwd=cwd or self.task, capture_output=True, text=True)

    def test_scoped_push_and_remote_sha(self):
        (self.task / "unrelated.txt").write_text("user work\n")
        result = self.push("--message", "chore: selected", "--file", "selected.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TASK_PUSH_RESULT status=success", result.stdout)
        self.assertEqual(self.run_git("show", "--format=", "--name-only", "HEAD", cwd=self.task), "selected.txt")
        self.assertEqual(self.run_git("ls-remote", "origin", "refs/heads/codex/task/test", cwd=self.task).split()[0], self.run_git("rev-parse", "HEAD", cwd=self.task))
        self.assertIn("?? unrelated.txt", self.run_git("status", "--short", cwd=self.task))

    def test_existing_index_is_preserved(self):
        self.run_git("add", "selected.txt", cwd=self.task)
        before = self.run_git("diff", "--cached", cwd=self.task)
        result = self.push("--message", "test", "--file", "selected.txt")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self.run_git("diff", "--cached", cwd=self.task))

    def test_forbidden_paths_and_directories_do_not_touch_index(self):
        (self.task / ".env").write_text("SECRET=fixture")
        for path in [".env", "scripts", "../main/selected.txt", ":(glob)*", "selected*"]:
            with self.subTest(path=path):
                result = self.push("--message", "test", "--file", path)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.run_git("diff", "--cached", "--name-only", cwd=self.task), "")

    def test_primary_and_non_task_branches_rejected(self):
        self.assertNotEqual(self.push("--message", "test", "--file", "selected.txt", cwd=self.main).returncode, 0)
        self.run_git("switch", "-c", "feature/unscoped", cwd=self.task)
        self.assertNotEqual(self.push("--message", "test", "--file", "selected.txt").returncode, 0)

    def test_deletion_and_filename_with_spaces(self):
        (self.task / "selected.txt").unlink()
        (self.task / "space name.txt").write_text("new\n")
        result = self.push("--message", "test", "--file", "selected.txt", "--file", "space name.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.run_git("show", "--format=", "--name-only", "HEAD", cwd=self.task).splitlines()), {"selected.txt", "space name.txt"})

    def test_failed_push_can_retry_same_commit(self):
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        result = self.push("--message", "test", "--file", "selected.txt")
        self.assertNotEqual(result.returncode, 0)
        sha = self.run_git("rev-parse", "HEAD", cwd=self.task)
        self.assertEqual(self.run_git("log", "-1", "--format=%s", cwd=self.task), "test")
        self.assertNotEqual(self.push("--push-commit", sha, "--file", "git-push.sh").returncode, 0)
        hook.unlink()
        result = self.push("--push-commit", sha, "--file", "selected.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sha, self.run_git("rev-parse", "HEAD", cwd=self.task))

    def test_private_key_content_not_pushed(self):
        (self.task / "selected.txt").write_text("-----BEGIN " + "OPENSSH PRIVATE KEY-----\nfixture\n")
        before = self.run_git("rev-parse", "HEAD", cwd=self.task)
        self.assertNotEqual(self.push("--message", "test", "--file", "selected.txt").returncode, 0)
        self.assertEqual(before, self.run_git("rev-parse", "HEAD", cwd=self.task))


if __name__ == "__main__":
    unittest.main()
