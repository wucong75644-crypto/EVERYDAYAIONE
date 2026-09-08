#!/usr/bin/env python3
"""Production coordination tests using only local Git remotes and a fake SSH."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[2]


class ReleaseCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="release-coordination-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.remote = self.root / "remote.git"
        # Exercise remote argument quoting, including shell metacharacters.
        self.production = self.root / "production ' quoted ; literal"
        self.production.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.ssh_log = self.root / "ssh-actions.log"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "FAKE_SSH_LOG": str(self.ssh_log),
            "FAKE_PRODUCTION": str(self.production),
        }
        ssh = self.bin / "ssh"
        ssh.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, shlex, subprocess, sys, time\n"
            "args = shlex.split(sys.argv[-1])\n"
            "assert sys.argv[-2] == 'test@example.invalid', sys.argv\n"
            "assert args[:3] == ['bash', '-s', '--'], args\n"
            "assert args[3] == os.environ['FAKE_PRODUCTION'], args\n"
            "action = args[4]\n"
            "with open(os.environ['FAKE_SSH_LOG'], 'a') as log: log.write(action + '\\n')\n"
            "if os.environ.get('FAKE_SSH_FAIL_BEFORE') == action: sys.exit(90)\n"
            "gate = os.environ.get('FAKE_READ_GATE')\n"
            "if action == 'read' and gate:\n"
            "    pathlib.Path(gate + '.entered').touch()\n"
            "    deadline = time.monotonic() + 15\n"
            "    while not pathlib.Path(gate + '.release').exists():\n"
            "        if time.monotonic() > deadline: sys.exit(92)\n"
            "        time.sleep(0.02)\n"
            "result = subprocess.run(args).returncode\n"
            "if os.environ.get('FAKE_SSH_FAIL_AFTER') == action: sys.exit(91)\n"
            "sys.exit(result)\n"
        )
        ssh.chmod(0o755)
        self.git("init", "--bare", str(self.remote), cwd=self.root)
        self.git("init", "-b", "main", str(self.repository), cwd=self.root)
        self.git("config", "user.name", "coordination-test")
        self.git("config", "user.email", "coordination@example.invalid")
        (self.repository / "deploy").mkdir()
        (self.repository / "scripts").mkdir()
        for name in ("deploy/release.sh", "deploy/release-coordination.sh", "scripts/task-worktree.sh"):
            shutil.copy2(SOURCE / name, self.repository / name)
        executor = self.repository / "deploy/deploy.sh"
        executor.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "source deploy/config.env\n"
            '[[ ! -e "$REMOTE_APP_DIR/.release-provenance" ]] || exit 93\n'
            'if [[ -n "${FAKE_REMOTE_WRITER_LAUNCHER:-}" ]]; then\n'
            '  "$FAKE_PYTHON" "$FAKE_REMOTE_WRITER_LAUNCHER"\n'
            '  exit 94\n'
            'fi\n'
            'printf "%s\\n" "$EVERYDAYAI_RELEASE_COMMIT" > "$REMOTE_APP_DIR/written"\n'
            'printf "%s\\n" "$EVERYDAYAI_RELEASE_CONTEXT" > "$REMOTE_APP_DIR/executor-context"\n'
            '# Simulate a legacy executor that records too early and then fails.\n'
            'if [[ "${FAIL_AFTER_WRITE:-}" == true ]]; then\n'
            '  printf "commit=%s\\n" "$EVERYDAYAI_RELEASE_COMMIT" > "$REMOTE_APP_DIR/.release-provenance"\n'
            '  exit 94\n'
            'fi\n'
            'echo "DEPLOY_RESULT status=success"\n'
        )
        executor.chmod(0o755)
        (self.repository / ".gitignore").write_text("deploy/config.env\n")
        (self.repository / "product.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        # Config is shell syntax, independently quoted from the SSH command.
        import shlex

        (self.repository / "deploy/config.env").write_text(
            "SERVER_HOST=example.invalid\nSERVER_USER=test\nSERVER_PORT=22\n"
            f"REMOTE_APP_DIR={shlex.quote(str(self.production))}\n"
        )

    def run_command(self, command: list[str], *, cwd: Path | None = None,
                    extra_env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(command, cwd=cwd or self.repository,
                                env={**self.env, **(extra_env or {})}, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        if check and result.returncode:
            self.fail(f"{command!r} exited {result.returncode}:\n{result.stdout}")
        return result

    def git(self, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_command(["git", *arguments], cwd=cwd)

    def task(self, name: str) -> Path:
        path = self.root / name
        self.run_command(["bash", "scripts/task-worktree.sh", "start", name, "--path", str(path)])
        return path

    def deploy(self, task: Path, name: str, *options: str,
               extra_env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        (task / f"{name}.txt").write_text(f"{name}\n")
        return self.run_command(["bash", "deploy/release.sh", "--message", f"test: {name}",
                                 "--file", f"{name}.txt", *options], cwd=task,
                                extra_env=extra_env, check=check)

    @property
    def marker(self) -> Path:
        return self.production / ".release-provenance"

    @property
    def lock(self) -> Path:
        return Path(f"{self.production}.release-lock")

    def test_partial_publish_invalidates_previous_candidate_and_blocks_acceptance(self) -> None:
        a, b = self.task("a"), self.task("b")
        self.deploy(a, "a")
        candidate = self.git("rev-parse", "HEAD", cwd=a).stdout.strip()
        self.assertIn(f"commit={candidate}", self.marker.read_text())
        self.assertEqual((self.production / "executor-context").read_text().strip(), "release.sh-legacy-executor")
        result = self.deploy(b, "b", "--frontend-only")
        self.assertIn("acceptance_candidate=false", result.stdout)
        self.assertIn("status_after=DEPLOYED_PARTIAL", result.stdout)
        self.assertFalse(self.marker.exists())
        result = self.run_command(["bash", "deploy/release.sh", "--accept-and-close"], cwd=a, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(a.exists())
        self.assertEqual(self.git("rev-parse", "origin/main").stdout.strip(), self.base)
        self.assertFalse(self.lock.exists())

    def test_failure_after_production_write_removes_even_legacy_candidate(self) -> None:
        a = self.task("a")
        self.marker.write_text(f"commit={self.base}\n")
        result = self.deploy(a, "failed", extra_env={"FAIL_AFTER_WRITE": "true"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.production / "written").exists())
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.lock.exists())
        self.assertIn("reason=executor_unconfirmed", result.stdout)
        self.assertTrue(a.exists())

    def test_async_remote_writer_keeps_failed_release_locked(self) -> None:
        a, b = self.task("a"), self.task("b")
        launcher = self.root / "launch-late-writer.py"
        worker = (
            "import os, pathlib, time\n"
            "root = pathlib.Path(os.environ['FAKE_PRODUCTION'])\n"
            "(root / 'late-writer-started').touch()\n"
            "deadline = time.monotonic() + 10\n"
            "while not (root / 'allow-late-write').exists():\n"
            "    if time.monotonic() > deadline: raise SystemExit(95)\n"
            "    time.sleep(0.02)\n"
            "(root / 'written').write_text(os.environ['EVERYDAYAI_RELEASE_COMMIT'] + '\\n')\n"
            "(root / 'late-writer-finished').touch()\n"
        )
        launcher.write_text(
            "import os, subprocess, sys\n"
            f"subprocess.Popen([sys.executable, '-c', {worker!r}], "
            "cwd=os.environ['FAKE_PRODUCTION'], start_new_session=True, "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        )
        try:
            result = self.deploy(a, "pending", extra_env={"FAKE_REMOTE_WRITER_LAUNCHER": str(launcher),
                                                         "FAKE_PYTHON": sys.executable}, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("executor_state=unconfirmed", result.stdout)
            self.assertTrue(self.lock.exists())
            result = self.deploy(b, "next", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("生产发布/验收锁", result.stdout)
            self.assertFalse((self.production / "written").exists())
            self.assertFalse(self.marker.exists())
        finally:
            (self.production / "allow-late-write").touch()
            deadline = time.monotonic() + 5
            while not (self.production / "late-writer-finished").exists():
                if time.monotonic() > deadline:
                    self.fail("the isolated delayed writer did not finish")
                time.sleep(0.02)
        self.assertEqual((self.production / "written").read_text().strip(),
                         self.git("rev-parse", "HEAD", cwd=a).stdout.strip())
        self.assertTrue(self.lock.exists())
        self.assertFalse(self.marker.exists())

    def test_acceptance_holds_same_lock_until_main_is_stable(self) -> None:
        a, b = self.task("a"), self.task("b")
        self.deploy(a, "a")
        candidate = self.git("rev-parse", "HEAD", cwd=a).stdout.strip()
        gate = self.root / "read-gate"
        output = self.root / "accept-output.log"
        with output.open("w") as stream:
            process = subprocess.Popen(["bash", "deploy/release.sh", "--accept-and-close"], cwd=a,
                                       env={**self.env, "FAKE_READ_GATE": str(gate)}, stdout=stream,
                                       stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 10
                while not Path(f"{gate}.entered").exists():
                    self.assertIsNone(process.poll(), output.read_text())
                    if time.monotonic() > deadline:
                        self.fail("acceptance did not reach the locked candidate read")
                    time.sleep(0.02)
                self.assertTrue(self.lock.exists())
                result = self.deploy(b, "b", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("生产发布/验收锁", result.stdout)
                self.assertEqual((self.production / "written").read_text().strip(), candidate)
                Path(f"{gate}.release").touch()
                self.assertEqual(process.wait(timeout=15), 0, output.read_text())
            finally:
                Path(f"{gate}.release").touch()
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
        self.assertFalse(a.exists())
        self.assertTrue(b.exists())
        self.assertFalse(self.lock.exists())
        final = self.git("rev-parse", "origin/main").stdout.strip()
        self.assertEqual(self.git("rev-parse", f"{final}^{{tree}}").stdout,
                         self.git("rev-parse", f"{candidate}^{{tree}}").stdout)
        self.assertEqual(self.git("config", "--worktree", "--get", "codex.taskStableBase", cwd=b).stdout.strip(), final)

    def test_unknown_acquisition_and_release_failures_keep_lock(self) -> None:
        a = self.task("a")
        result = self.deploy(a, "uncertain", extra_env={"FAKE_SSH_FAIL_AFTER": "acquire"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.lock / "owner").exists())
        self.assertFalse((self.production / "written").exists())
        # This fixture alone owns the fake lock and deliberately resets it.
        shutil.rmtree(self.lock)
        result = self.deploy(a, "release-failure", extra_env={"FAKE_SSH_FAIL_BEFORE": "release"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RELEASE_RESULT status=success", result.stdout)
        self.assertIn("RELEASE_LOCK_RESULT status=retained operation_exit=0", result.stdout)
        self.assertTrue(self.marker.exists())
        self.assertTrue((self.lock / "owner").exists())

    def test_unknown_owner_is_never_released_and_invalidation_failure_never_deploys(self) -> None:
        a = self.task("a")
        self.lock.mkdir()
        (self.lock / "owner").write_text("another-operation\n")
        result = self.deploy(a, "busy", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.lock / "owner").read_text(), "another-operation\n")
        self.assertFalse((self.production / "written").exists())
        shutil.rmtree(self.lock)
        self.marker.write_text(f"commit={self.base}\n")
        result = self.deploy(a, "invalidate-failed", extra_env={"FAKE_SSH_FAIL_BEFORE": "invalidate"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.production / "written").exists())
        self.assertEqual(self.marker.read_text(), f"commit={self.base}\n")
        self.assertTrue(self.lock.exists())
        self.assertIn("reason=production_state_unconfirmed", result.stdout)

    def test_candidate_record_failure_preserves_completed_deployment_and_lock(self) -> None:
        a = self.task("a")
        result = self.deploy(a, "record-failed", extra_env={"FAKE_SSH_FAIL_BEFORE": "record"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("executor_state=completed", result.stdout)
        self.assertIn("reason=production_state_unconfirmed", result.stdout)
        self.assertTrue((self.production / "written").exists())
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.lock.exists())

    def test_main_push_uncertainty_keeps_acceptance_lock_and_task(self) -> None:
        a, b = self.task("a"), self.task("b")
        self.deploy(a, "a")
        real_git = shutil.which("git", path=os.environ["PATH"])
        self.assertIsNotNone(real_git)
        wrapper = self.bin / "git"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, subprocess, sys\n"
            f"result = subprocess.run([{real_git!r}, *sys.argv[1:]]).returncode\n"
            "if os.environ.get('FAKE_FAIL_MAIN_PUSH') == 'true' and 'HEAD:refs/heads/main' in sys.argv:\n"
            "    sys.exit(96)\n"
            "sys.exit(result)\n"
        )
        wrapper.chmod(0o755)
        result = self.run_command(["bash", "deploy/release.sh", "--accept-and-close"], cwd=a,
                                  extra_env={"FAKE_FAIL_MAIN_PUSH": "true"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reason=stable_main_unconfirmed", result.stdout)
        self.assertIn("main_write_unconfirmed=true", result.stdout)
        self.assertTrue(a.exists())
        self.assertTrue(self.lock.exists())
        # The remote ref actually advanced, despite the unconfirmed push result.
        remote_main = self.git("ls-remote", "origin", "refs/heads/main").stdout.split()[0]
        self.assertNotEqual(remote_main, self.base)
        result = self.deploy(b, "blocked", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("生产发布/验收锁", result.stdout)
        self.assertEqual((self.production / "written").read_text().strip(),
                         self.git("rev-parse", "HEAD", cwd=a).stdout.strip())

    def test_current_executor_rejects_uncontrolled_execution(self) -> None:
        result = self.run_command(["bash", str(SOURCE / "deploy/deploy.sh"), "--frontend-only"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必须通过具备生产协调保护", result.stdout)
        self.assertFalse(self.ssh_log.exists())
        self.assertFalse((self.production / "written").exists())

    def test_current_executor_rejects_wrong_lock_owner(self) -> None:
        self.lock.mkdir()
        (self.lock / "owner").write_text("actual-owner\n")
        result = self.run_command(
            ["bash", str(SOURCE / "deploy/deploy.sh"), "--frontend-only"],
            extra_env={"EVERYDAYAI_RELEASE_CONTEXT": "release.sh",
                       "EVERYDAYAI_RELEASE_PROTOCOL": "2",
                       "EVERYDAYAI_RELEASE_LOCK_TOKEN": "wrong-owner"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("发布锁所有权无法确认", result.stdout)
        self.assertEqual(self.ssh_log.read_text().strip(), "assert")
        self.assertEqual((self.lock / "owner").read_text(), "actual-owner\n")
        self.assertFalse((self.production / "written").exists())


if __name__ == "__main__":
    unittest.main()
