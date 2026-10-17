"""Cloud tooling invariants; all Git activity uses a temporary local remote."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cloud_dev", ROOT / "scripts/cloud/dev.py")
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


class DevelopmentConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "backend").mkdir()
        (self.root / "scripts/cloud").mkdir(parents=True)
        (self.root / "scripts/cloud/development.env.example").write_text(
            (ROOT / "scripts/cloud/development.env.example").read_text()
        )
        for key, value in {"ROOT": self.root, "BACKEND": self.root / "backend", "STATE": self.root / ".cloud-dev"}.items():
            mock = patch.object(dev, key, value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_init_private_random_and_idempotent(self):
        dev.init()
        path = self.root / "backend/.env"
        text = path.read_text()
        self.assertNotIn("GENERATED_BY_INIT", text)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn(str(self.root / ".cloud-dev/workspace"), text)
        dev.init()
        self.assertEqual(path.read_text(), text)

    def test_root_secrets_not_copied(self):
        (self.root / ".env").write_text("PRODUCTION_SECRET=do-not-copy\n")
        dev.init()
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("PRODUCTION_SECRET", dev.environment())

    def test_process_environment_wins(self):
        dev.init()
        with patch.dict(os.environ, {"REDIS_DB": "12"}, clear=True):
            self.assertEqual(dev.environment()["REDIS_DB"], "12")

    def test_missing_config_fails(self):
        with self.assertRaisesRegex(RuntimeError, "init"):
            dev.environment()

    def test_failed_check_does_not_print_credentials(self):
        result = subprocess.CompletedProcess([], 1, "", "postgresql://secret:password@host/db")
        with patch.object(dev.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as error:
                dev.check_backend({})
        self.assertNotIn("password", str(error.exception))
        self.assertEqual((self.root / ".cloud-dev/check.log").stat().st_mode & 0o777, 0o600)


class WorktreeTests(unittest.TestCase):
    def test_development_without_deploy_secrets_and_local_default(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote, seed = base / "remote.git", base / "repo"
            def run(*args, cwd=None):
                return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)
            run("git", "init", "--bare", str(remote))
            run("git", "init", "-b", "main", str(seed))
            run("git", "config", "user.name", "test", cwd=seed)
            run("git", "config", "user.email", "test@example.invalid", cwd=seed)
            (seed / "scripts").mkdir()
            (seed / "deploy").mkdir()
            (seed / "deploy/.gitkeep").touch()
            script = seed / "scripts/task-worktree.sh"
            script.write_text((ROOT / "scripts/task-worktree.sh").read_text())
            run("git", "add", ".", cwd=seed)
            run("git", "commit", "-m", "fixture", cwd=seed)
            run("git", "remote", "add", "origin", str(remote), cwd=seed)
            run("git", "push", "origin", "main", cwd=seed)
            denied = subprocess.run(["bash", str(script), "start", "local"], cwd=seed, capture_output=True)
            self.assertNotEqual(denied.returncode, 0)
            cloud = base / "cloud"
            run("bash", str(script), "start", "cloud", "--development-only", "--path", str(cloud), cwd=seed)
            self.assertFalse((cloud / "deploy/config.env").exists())
            self.assertTrue(run("git", "branch", "--show-current", cwd=cloud).stdout.startswith("codex/task/"))
            self.assertEqual(run("git", "config", "--worktree", "codex.taskStableBase", cwd=cloud).stdout.strip(), run("git", "rev-parse", "HEAD", cwd=seed).stdout.strip())
            (seed / "deploy/config.env").write_text("LOCAL_DEPLOY_SECRET=fixture\n")
            # Even when local credentials exist, development-only must not copy them.
            cloud2 = base / "cloud2"
            run("bash", str(script), "start", "cloud-two", "--development-only", "--path", str(cloud2), cwd=seed)
            self.assertFalse((cloud2 / "deploy/config.env").exists())
            local = base / "local"
            run("bash", str(script), "start", "local", "--path", str(local), cwd=seed)
            self.assertEqual((local / "deploy/config.env").read_text(), "LOCAL_DEPLOY_SECRET=fixture\n")
            self.assertEqual((local / "deploy/config.env").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
