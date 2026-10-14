"""Exercise the one-command incident repair without contacting Redis or production."""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("redis_streaming_repair", SOURCE / "deploy/repair-redis-streaming.py")
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class RedisDouble:
    def __init__(self, **changes):
        self.replication = {"role": "slave", "master_host": "203.0.113.77", "master_port": 60100, "connected_slaves": 0}
        self.replication.update(changes)
        self.config = {"dir": "/etc", "dbfilename": "crontab"}
        self.run_id = "same-instance"
        self.commands = []
        self.config_reads = []
        self.error = None
        self.finish_role = "master"
        self.finish_run_id = self.run_id

    def info(self, section):
        return self.replication.copy() if section == "replication" else {"run_id": self.run_id}

    def config_get(self, field):
        self.config_reads.append(field)
        return {field: self.config[field]}

    def execute_command(self, *args):
        self.commands.append(args)
        if self.error:
            raise self.error
        self.replication["role"] = self.finish_role
        self.run_id = self.finish_run_id
        return "OK"

    def close(self):
        pass


class RedisStreamingRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app_dir = Path(self.temporary.name) / "app"
        (self.app_dir / "backend").mkdir(parents=True)
        self.env_file = self.app_dir / "backend/.env"
        self.original_env = b"REDIS_HOST=127.0.0.1\nREDIS_PASSWORD=private-test-password\n"
        self.env_file.write_bytes(self.original_env)
        self.lock_dir = Path(f"{self.app_dir}.release-lock")
        self.lock_dir.mkdir()
        (self.lock_dir / "owner").write_text("test-owner\n")

    def run_repair(self, client, *, apply=True, token="test-owner"):
        return repair.repair(self.app_dir, apply=apply, lock_token=token,
                             client_factory=lambda _: (client, "127.0.0.1"))

    def evidence(self):
        return list((self.app_dir / ".incident-evidence").glob("*.json"))

    def test_verified_incident_runs_exactly_one_command_and_preserves_configuration(self):
        client = RedisDouble()
        result, code = self.run_repair(client)
        self.assertEqual(code, 0)
        self.assertEqual(result, {"status": "updated", "role": "master", "no_data_cleared": True})
        self.assertEqual(client.commands, [("REPLICAOF", "NO", "ONE")])
        self.assertEqual(self.env_file.read_bytes(), self.original_env)
        self.assertEqual((self.lock_dir / "owner").read_text(), "test-owner\n")
        self.assertEqual(client.config, {"dir": "/etc", "dbfilename": "crontab"})
        self.assertEqual(len(self.evidence()), 1)
        path = self.evidence()[0]
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        metadata = json.loads(path.read_text())
        self.assertEqual(set(metadata), {"run_id", "role", "master_host", "master_port", "connected_slaves", "dir", "dbfilename"})
        self.assertNotIn("private-test-password", path.read_text())

    def test_dry_run_neither_writes_evidence_nor_requires_lock(self):
        (self.lock_dir / "owner").unlink()
        self.lock_dir.rmdir()
        client = RedisDouble()
        result, code = self.run_repair(client, apply=False, token=None)
        self.assertEqual((code, result["status"]), (0, "dry_run"))
        self.assertEqual(client.commands, [])
        self.assertFalse((self.app_dir / ".incident-evidence").exists())
        self.assertEqual(self.env_file.read_bytes(), self.original_env)

    def test_existing_master_does_not_read_or_change_config(self):
        client = RedisDouble(role="master")
        result, code = self.run_repair(client)
        self.assertEqual((code, result["status"]), (0, "nochange"))
        self.assertEqual(client.commands, [])
        self.assertEqual(client.config_reads, [])
        self.assertEqual(self.evidence(), [])

    def test_ordinary_ha_and_downstream_replicas_are_refused(self):
        for changes in ({"master_port": 6379}, {"master_host": ""}, {"master_host": "127.0.0.1"},
                        {"connected_slaves": 1}, {"role": "unknown"}):
            with self.subTest(changes=changes):
                client = RedisDouble(**changes)
                result, code = self.run_repair(client)
                self.assertEqual(code, 1)
                self.assertEqual(result["status"], "error")
                self.assertEqual(client.commands, [])
        self.assertEqual(self.evidence(), [])

    def test_different_directory_or_filename_is_refused(self):
        for field, value in (("dir", "/tmp"), ("dbfilename", "dump.rdb")):
            with self.subTest(field=field):
                client = RedisDouble()
                client.config[field] = value
                result, code = self.run_repair(client)
                self.assertEqual((code, result["reason"]), (1, "incident_guard_mismatch"))
                self.assertEqual(client.commands, [])
        self.assertEqual(self.evidence(), [])

    def test_missing_and_wrong_owner_lock_are_refused_before_connecting(self):
        factory = unittest.mock.Mock(side_effect=AssertionError("must not connect"))
        for token in (None, "wrong-owner"):
            result, code = repair.repair(self.app_dir, apply=True, lock_token=token, client_factory=factory)
            self.assertEqual((code, result["reason"]), (1, "release_lock_required"))
        factory.assert_not_called()

    def test_changed_lock_on_second_check_refuses_mutation(self):
        client = RedisDouble()
        original = repair.verify_lock
        count = 0

        def change_lock(app_dir, token):
            nonlocal count
            count += 1
            if count == 2:
                (self.lock_dir / "owner").write_text("another-owner\n")
            return original(app_dir, token)

        with patch.object(repair, "verify_lock", side_effect=change_lock):
            result, code = self.run_repair(client)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(client.commands, [])
        self.assertEqual(self.evidence(), [])

    def test_changed_lock_after_evidence_refuses_mutation_and_keeps_evidence(self):
        client = RedisDouble()
        original = repair.save_evidence

        def write_then_change(app_dir, metadata):
            original(app_dir, metadata)
            (self.lock_dir / "owner").write_text("another-owner\n")

        with patch.object(repair, "save_evidence", side_effect=write_then_change):
            result, code = self.run_repair(client)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(client.commands, [])
        self.assertEqual(len(self.evidence()), 1)

    def test_command_failure_does_not_retry_or_report_success_or_disclose_exception(self):
        client = RedisDouble()
        client.error = TimeoutError("private-test-password redis://203.0.113.77:60100/private-body")
        result, code = self.run_repair(client)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "command_failed_state_unknown")
        self.assertEqual(client.commands, [("REPLICAOF", "NO", "ONE")])
        self.assertEqual(len(self.evidence()), 1)
        output = json.dumps(result)
        for private in ("private-test-password", "203.0.113.77", "redis://", "private-body", "same-instance"):
            self.assertNotIn(private, output)

    def test_postcondition_requires_master_and_same_instance(self):
        for field, value in (("finish_role", "slave"), ("finish_run_id", "changed-instance")):
            with self.subTest(field=field):
                client = RedisDouble()
                setattr(client, field, value)
                result, code = self.run_repair(client)
                self.assertEqual((code, result["reason"]), (1, "postcondition_failed"))
                self.assertEqual(client.commands, [("REPLICAOF", "NO", "ONE")])

    def test_timeout_after_execution_is_verified_but_still_returns_nonzero(self):
        client = RedisDouble()

        def executed_then_timed_out(*args):
            client.commands.append(args)
            client.replication["role"] = "master"
            raise TimeoutError("private-response")

        client.execute_command = executed_then_timed_out
        result, code = self.run_repair(client)
        self.assertEqual((code, result["status"], result["role"]), (1, "error", "master"))
        self.assertEqual(result["reason"], "command_failed_state_verified")
        self.assertEqual(client.commands, [("REPLICAOF", "NO", "ONE")])
        self.assertTrue((self.lock_dir / "owner").exists())

    def test_self_alias_is_refused(self):
        client = RedisDouble()
        with patch.object(repair, "_addresses", return_value={"192.0.2.10"}):
            result, code = self.run_repair(client)
        self.assertEqual((code, result["reason"]), (1, "incident_guard_mismatch"))
        self.assertEqual(client.commands, [])

    def test_evidence_failure_prevents_command(self):
        client = RedisDouble()
        with patch.object(repair, "save_evidence", side_effect=PermissionError("private-path")):
            result, code = self.run_repair(client)
        self.assertEqual(code, 1)
        self.assertEqual(client.commands, [])
        self.assertNotIn("private-path", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
