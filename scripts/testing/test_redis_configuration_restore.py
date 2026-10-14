"""Incident-only persistent Redis restoration, with all external IO isolated."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("redis_configuration_restore", SOURCE / "deploy/restore-redis-configuration.py")
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)


class RedisDouble:
    def __init__(self, config_file):
        self.replication = {"role": "master", "connected_slaves": 0}
        self.config = {"dir": "/etc", "dbfilename": "crontab", "replica-read-only": "no"}
        self.server = {"run_id": "same-instance", "config_file": str(config_file), "process_id": 123}
        self.commands = []
        self.fail_command = None
        self.post_role = None

    def info(self, section):
        return self.replication.copy() if section == "replication" else self.server.copy()

    def config_get(self, field):
        return {field: self.config[field]}

    def execute_command(self, *args):
        self.commands.append(args)
        if len(self.commands) == self.fail_command:
            raise TimeoutError("private-password private-body 203.0.113.77")
        command, operation, field, value = args
        assert (command, operation) == ("CONFIG", "SET")
        self.config[field] = value
        if len(self.commands) == 3 and self.post_role:
            self.replication["role"] = self.post_role
        return "OK"

    def close(self):
        pass


class RedisConfigurationRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.app = root / "app"
        (self.app / "backend").mkdir(parents=True)
        self.env = self.app / "backend/.env"
        self.env.write_bytes(b"REDIS_PASSWORD=private-password\n")
        self.data = root / "redis-data"
        self.data.mkdir(mode=0o750)
        self.dump = self.data / "dump.rdb"
        self.dump.write_bytes(b"opaque-original-rdb\x00\xff")
        self.config_file = root / "redis.conf"
        self.original = (b"# untouched\r\nrequirepass private-password\r\n"
                         b"  dir \"/tmp\"   # directory comment\r\n"
                         b"dbfilename exp.so\r\nreplicaof 203.0.113.77 7178\r\n"
                         b"replica-read-only no\r\nappendonly no\r\n")
        self.config_file.write_bytes(self.original)
        self.config_file.chmod(0o640)
        self.lock = Path(f"{self.app}.release-lock")
        self.lock.mkdir()
        (self.lock / "owner").write_text("test-owner\n")
        self.client = RedisDouble(self.config_file)
        for patcher in (patch.object(restore, "DATA_DIR", self.data),
                        patch.object(restore, "verify_service", return_value=os.getuid())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_restore(self, *, apply=True, token="test-owner"):
        return restore.restore(self.app, apply=apply, lock_token=token,
                               client_factory=lambda _: (self.client, "127.0.0.1"))

    def backups(self):
        return list((self.app / ".incident-evidence").glob("*.bak"))

    def expected_config(self):
        return self.original.replace(b'"/tmp"', str(self.data).encode()).replace(b"exp.so", b"dump.rdb").replace(
            b"replicaof 203.0.113.77 7178\r\n", b"").replace(b"replica-read-only no", b"replica-read-only yes")

    def test_incident_is_backed_up_and_only_three_config_commands_are_executed(self):
        before = self.config_file.stat()
        dump_before = self.dump.read_bytes()
        result, code = self.run_restore()
        self.assertEqual((code, result["status"]), (0, "updated"))
        self.assertEqual(self.client.commands, [
            ("CONFIG", "SET", "dir", str(self.data)),
            ("CONFIG", "SET", "dbfilename", "dump.rdb"),
            ("CONFIG", "SET", "replica-read-only", "yes"),
        ])
        self.assertEqual(self.config_file.read_bytes(), self.expected_config())
        after = self.config_file.stat()
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (before.st_uid, before.st_gid, 0o640))
        self.assertEqual(self.dump.read_bytes(), dump_before)
        self.assertEqual(self.env.read_bytes(), b"REDIS_PASSWORD=private-password\n")
        self.assertEqual(len(self.backups()), 2)
        self.assertEqual({path.read_bytes() for path in self.backups()}, {self.original, dump_before})
        for path in self.backups():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual((self.lock / "owner").read_text(), "test-owner\n")

    def test_dry_run_has_no_writes_and_does_not_require_lock(self):
        (self.lock / "owner").unlink()
        self.lock.rmdir()
        result, code = self.run_restore(apply=False, token=None)
        self.assertEqual((code, result["status"]), (0, "dry_run"))
        self.assertEqual(self.config_file.read_bytes(), self.original)
        self.assertEqual(self.client.commands, [])
        self.assertFalse((self.app / ".incident-evidence").exists())

    def test_already_correct_configuration_is_nochange(self):
        self.config_file.write_bytes(self.expected_config())
        self.client.config = {"dir": str(self.data), "dbfilename": "dump.rdb", "replica-read-only": "yes"}
        result, code = self.run_restore()
        self.assertEqual((code, result["status"]), (0, "nochange"))
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.backups(), [])

    def test_running_replica_or_attached_replicas_are_refused(self):
        for change in ({"role": "slave"}, {"connected_slaves": 1}):
            with self.subTest(change=change):
                self.client.replication = {"role": "master", "connected_slaves": 0, **change}
                result, code = self.run_restore()
                self.assertEqual((code, result["reason"]), (1, "master_without_replicas_required"))
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.original)

    def test_different_persistent_or_runtime_incidents_are_refused(self):
        for old, new in ((b"dir \"/tmp\"", b"dir /other"), (b"exp.so", b"dump.rdb"),
                         (b"7178", b"6379"), (b"replica-read-only no", b"replica-read-only yes"),
                         (b"203.0.113.77", b"127.0.0.1")):
            with self.subTest(old=old):
                self.config_file.write_bytes(self.original.replace(old, new))
                result, code = self.run_restore()
                self.assertEqual((code, result["reason"]), (1, "incident_guard_mismatch"))
        self.config_file.write_bytes(self.original)
        self.client.config["dir"] = "/other"
        result, code = self.run_restore()
        self.assertEqual((code, result["reason"]), (1, "incident_guard_mismatch"))
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.backups(), [])

    def test_duplicate_alias_or_active_include_is_refused(self):
        for extra in (b"dir /tmp\n", b"slaveof 203.0.113.77 7178\n", b"include another.conf\n"):
            with self.subTest(extra=extra):
                self.config_file.write_bytes(self.original + extra)
                result, code = self.run_restore()
                self.assertEqual(code, 1)
                self.assertEqual(result["status"], "error")
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.backups(), [])

    def test_data_directory_must_exist_be_owned_by_redis_and_not_world_writable(self):
        for mode in (0o752, 0o777):
            with self.subTest(mode=mode):
                self.data.chmod(mode)
                result, code = self.run_restore()
                self.assertEqual((code, result["reason"]), (1, "data_directory_unsafe"))
        self.data.chmod(0o750)
        with patch.object(restore, "verify_service", return_value=os.getuid() + 1):
            result, code = self.run_restore()
        self.assertEqual((code, result["reason"]), (1, "data_directory_unsafe"))
        self.assertEqual(self.client.commands, [])

    def test_config_or_data_directory_symlink_is_refused(self):
        other = self.config_file.with_suffix(".original")
        self.config_file.rename(other)
        self.config_file.symlink_to(other)
        result, code = self.run_restore()
        self.assertEqual(code, 1)
        self.assertEqual(self.client.commands, [])
        self.config_file.unlink()
        other.rename(self.config_file)
        other_dir = self.data.with_name("other-data")
        self.data.rename(other_dir)
        self.data.symlink_to(other_dir, target_is_directory=True)
        result, code = self.run_restore()
        self.assertEqual((code, result["reason"]), (1, "data_directory_unsafe"))
        self.assertEqual(self.client.commands, [])

    def test_missing_lock_is_refused_before_connecting(self):
        factory = Mock(side_effect=AssertionError("must not connect"))
        result, code = restore.restore(self.app, apply=True, client_factory=factory)
        self.assertEqual((code, result["reason"]), (1, "release_lock_required"))
        factory.assert_not_called()

    def test_lock_changed_after_backup_prevents_all_mutations(self):
        original = restore.save_backups

        def save_then_change(*args):
            original(*args)
            (self.lock / "owner").write_text("another-owner\n")

        with patch.object(restore, "save_backups", side_effect=save_then_change):
            result, code = self.run_restore()
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.config_file.read_bytes(), self.original)
        self.assertEqual(self.client.commands, [])
        self.assertEqual(len(self.backups()), 2)

    def test_backup_failure_prevents_all_mutations(self):
        with patch.object(restore, "save_backups", side_effect=PermissionError("private-path")):
            result, code = self.run_restore()
        self.assertEqual(code, 1)
        self.assertEqual(self.config_file.read_bytes(), self.original)
        self.assertEqual(self.client.commands, [])
        self.assertNotIn("private-path", json.dumps(result))

    def test_command_failure_is_not_retried_or_rolled_back_to_malicious_configuration(self):
        self.client.fail_command = 2
        result, code = self.run_restore()
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertEqual(len(self.client.commands), 2)
        self.assertEqual(self.config_file.read_bytes(), self.expected_config())
        self.assertEqual(len(self.backups()), 2)
        self.assertTrue((self.lock / "owner").exists())
        for private in ("private-password", "private-body", "203.0.113.77", str(self.config_file)):
            self.assertNotIn(private, json.dumps(result))

    def test_runtime_changes_during_backup_prevent_all_config_commands(self):
        original = restore.save_backups
        for change in ("replica", "new_instance", "downstream"):
            with self.subTest(change=change):
                self.config_file.write_bytes(self.original)
                self.client = RedisDouble(self.config_file)

                def save_then_change(*args):
                    original(*args)
                    if change == "replica":
                        self.client.replication["role"] = "slave"
                    elif change == "downstream":
                        self.client.replication["connected_slaves"] = 1
                    else:
                        self.client.server["run_id"] = "new-instance"

                with patch.object(restore, "save_backups", side_effect=save_then_change):
                    result, code = self.run_restore()
                self.assertEqual((code, result["reason"]), (1, "runtime_identity_changed"))
                self.assertEqual(self.client.commands, [])
                self.assertEqual(self.config_file.read_bytes(), self.expected_config())
                self.assertTrue((self.lock / "owner").exists())
        self.assertEqual(len(self.backups()), 6)

    def test_runtime_identity_is_checked_again_before_second_config_command(self):
        original = self.client.execute_command

        def set_then_attach(*args):
            result = original(*args)
            self.client.replication["role"] = "slave"
            return result

        self.client.execute_command = set_then_attach
        result, code = self.run_restore()
        self.assertEqual((code, result["reason"]), (1, "runtime_identity_changed"))
        self.assertEqual(self.client.commands, [("CONFIG", "SET", "dir", str(self.data))])
        self.assertEqual(len(self.backups()), 2)
        self.assertTrue((self.lock / "owner").exists())

    def test_final_role_verification_is_required(self):
        self.client.post_role = "slave"
        result, code = self.run_restore()
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertEqual(len(self.client.commands), 3)
        self.assertTrue((self.lock / "owner").exists())

    def test_actual_service_user_pid_and_process_owner_must_match(self):
        # setUp stubs this function for the other filesystem tests; load a fresh
        # module here so this case exercises its actual systemctl/PID checks.
        spec = importlib.util.spec_from_file_location("restore_service_check", SPEC.origin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for output, uid in (("User=root\nMainPID=123\n", os.getuid()),
                            ("User=redis\nMainPID=999\n", os.getuid()),
                            ("User=redis\nMainPID=123\n", os.getuid() + 1)):
            with self.subTest(output=output), patch.object(module.subprocess, "run", return_value=SimpleNamespace(stdout=output)), \
                    patch.object(module.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid())), \
                    patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=uid)):
                with self.assertRaises(module.Refused):
                    module.verify_service(self.client.server)


if __name__ == "__main__":
    unittest.main()
