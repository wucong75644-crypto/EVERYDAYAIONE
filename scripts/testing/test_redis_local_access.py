"""Ordering and refusal tests for the incident-only loopback migration."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("redis_local_access", SOURCE / "deploy/secure-redis-local-access.py")
secure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secure)
PUBLIC = "8.8.8.8"  # Synthetic identity only; every network operation is replaced.
REAL_HEALTH = secure.ensure_health
REAL_IDLE = secure.ensure_idle
REAL_LISTENERS = secure.verify_listeners


class RedisDouble:
    def __init__(self, case, *, public=False):
        self.case, self.public, self.closed = case, public, False
        self.run_id = "same-instance"
        self.bind = "0.0.0.0"
        self.commands = []

    def info(self, section):
        if section == "replication":
            return {"role": "master", "connected_slaves": 0}
        return {"run_id": self.run_id, "tcp_port": 6379, "process_id": 123,
                "config_file": str(self.case.config_file)}

    def config_get(self, field):
        assert field == "bind"
        return {field: self.bind}

    def client_list(self):
        self.case.trace.append("clients")
        self.case.assertTrue(self.case.public.closed)
        if self.case.foreign:
            return [{"addr": "9.9.9.9:1234"}]
        if any(host != "127.0.0.1" for host in self.case.hosts.values()) or self.case.public_lingers:
            return [{"addr": f"{PUBLIC}:1234"}, {"addr": "127.0.0.1:5678"}]
        return [{"addr": "127.0.0.1:5678"}]

    def execute_command(self, *args):
        self.case.trace.append("bind")
        self.commands.append(args)
        self.case.assertEqual(args, ("CONFIG", "SET", "bind", "127.0.0.1"))
        self.case.assertEqual(self.case.restarted, self.case.expected_restarts)
        self.case.assertTrue(all(host == "127.0.0.1" for host in self.case.hosts.values()))
        self.case.assertIn(b"bind 127.0.0.1", self.case.config_file.read_bytes())
        if self.case.bind_fails:
            raise TimeoutError("private-password private-body")
        self.bind = "127.0.0.1"
        return "OK"

    def close(self):
        self.closed = True
        self.case.trace.append("public-close" if self.public else "local-close")


class RedisLocalAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Path(temporary.name) / "app"
        (self.app / "backend").mkdir(parents=True)
        self.env_file = self.app / "backend/.env"
        self.env_original = (b"# untouched\r\nREDIS_PASSWORD='private-password'\r\n"
                             b" export REDIS_HOST = '8.8.8.8'  # keep comment\r\n"
                             b"REDIS_PORT=6379\r\nREDIS_SSL=false\r\nOTHER=value\r\n")
        self.env_file.write_bytes(self.env_original)
        self.env_file.chmod(0o640)
        self.config_file = self.app.parent / "redis.conf"
        self.conf_original = b"# untouched\r\n  bind 0.0.0.0  # retain\r\nport 6379\r\nrequirepass private-password\r\n"
        self.config_file.write_bytes(self.conf_original)
        self.config_file.chmod(0o640)
        self.lock_dir = Path(f"{self.app}.release-lock")
        self.lock_dir.mkdir()
        (self.lock_dir / "owner").write_text("test-owner\n")
        self.values = {"REDIS_HOST": PUBLIC, "REDIS_PORT": "6379", "REDIS_SSL": "false", "REDIS_PASSWORD": "private-password"}
        self.trace, self.restarted = [], []
        self.expected_restarts = list(secure.UNITS)
        self.clock = 0
        self.foreign = self.public_lingers = self.bind_fails = False
        self.restart_failure = self.active_failure = None
        self.busy_before_restart = None
        self.pid_by_unit = {unit: index + 200 for index, unit in enumerate(secure.UNITS)}
        self.hosts = {pid: PUBLIC for pid in self.pid_by_unit.values()}
        self.properties = {unit: {"ActiveState": "active", "MainPID": str(pid),
                                 "EnvironmentFiles": f"{self.env_file} (ignore_errors=no)",
                                 "PrivateNetwork": "no", "NetworkNamespacePath": "", "JoinsNamespaceOf": ""}
                           for unit, pid in self.pid_by_unit.items()}
        self.local, self.public = RedisDouble(self), RedisDouble(self, public=True)
        patchers = [
            patch.dict(sys.modules, {"dotenv": SimpleNamespace(dotenv_values=lambda *_args, **_kw: self.values.copy())}),
            patch.object(secure, "connect", side_effect=lambda _values, host: self.local if host == "127.0.0.1" else self.public),
            patch.object(secure, "addresses", side_effect=lambda host: {host}),
            patch.object(secure, "unit_properties", side_effect=self.unit_properties),
            patch.object(secure, "process_host", side_effect=lambda pid: self.hosts[pid]),
            patch.object(secure, "verify_redis_unit", side_effect=lambda _server: self.trace.append("redis-unit")),
            patch.object(secure, "command", side_effect=self.command),
            patch.object(secure, "ensure_health", side_effect=lambda: self.trace.append("health")),
            patch.object(secure, "ensure_idle", side_effect=self.ensure_idle),
            patch.object(secure, "verify_listeners", side_effect=lambda *_args: self.trace.append("listeners")),
            patch.object(secure.time, "monotonic", side_effect=lambda: self.clock),
            patch.object(secure.time, "sleep", side_effect=self.advance_clock),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def unit_properties(self, unit):
        self.trace.append("verify:" + unit)
        values = self.properties[unit].copy()
        if unit == self.active_failure and unit in self.restarted:
            values["ActiveState"] = "failed"
        return values

    def advance_clock(self, seconds):
        self.clock += seconds

    def ensure_idle(self, _values):
        self.trace.append("idle")
        if self.busy_before_restart is not None and len(self.restarted) == self.busy_before_restart:
            raise secure.Refused("running_work_present")

    def command(self, args, timeout=10):
        self.assertEqual(args[:2], ["systemctl", "restart"])
        unit = args[2]
        self.trace.append("restart:" + unit)
        self.assertEqual(self.env_file.read_bytes(), self.env_original.replace(PUBLIC.encode(), b"127.0.0.1"))
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)
        if unit == self.restart_failure:
            raise subprocess.TimeoutExpired(args, timeout)
        self.restarted.append(unit)
        self.hosts[self.pid_by_unit[unit]] = "127.0.0.1"
        return ""

    def run_secure(self, *, apply=True, token="test-owner"):
        return secure.secure(self.app, PUBLIC, apply=apply, lock_token=token)

    def backups(self):
        return list((self.app / ".incident-evidence").glob("*.bak"))

    def test_migrate_and_verify_all_clients_before_closing_port(self):
        before = self.env_file.stat()
        result, code = self.run_secure()
        self.assertEqual((code, result["status"]), (0, "updated"))
        self.assertEqual(self.restarted, list(secure.UNITS))
        self.assertEqual(self.local.commands, [("CONFIG", "SET", "bind", "127.0.0.1")])
        self.assertEqual(self.env_file.read_bytes(), self.env_original.replace(PUBLIC.encode(), b"127.0.0.1"))
        self.assertEqual(self.config_file.read_bytes(), self.conf_original.replace(b"0.0.0.0", b"127.0.0.1"))
        after = self.env_file.stat()
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)), (before.st_uid, before.st_gid, 0o640))
        self.assertEqual(stat.S_IMODE(self.config_file.stat().st_mode), 0o640)
        self.assertLess(self.trace.index("public-close"), self.trace.index("clients"))
        self.assertLess(self.trace.index("restart:" + secure.UNITS[-1]), self.trace.index("bind"))
        self.assertEqual({path.read_bytes() for path in self.backups()}, {self.env_original, self.conf_original})
        for path in self.backups():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual((self.lock_dir / "owner").read_text(), "test-owner\n")

    def test_dryrun_performs_no_writes_or_restarts(self):
        result, code = self.run_secure(apply=False, token=None)
        self.assertEqual((code, result["status"]), (0, "dry_run"))
        self.assertEqual(self.restarted, [])
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.env_file.read_bytes(), self.env_original)
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)
        self.assertEqual(self.backups(), [])

    def test_foreign_clients_are_rejected_before_any_write(self):
        self.foreign = True
        result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "foreign_clients_present"))
        self.assertEqual(self.restarted, [])
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.env_file.read_bytes(), self.env_original)
        self.assertEqual(self.backups(), [])

    def test_different_public_instance_is_rejected(self):
        self.public.run_id = "different-instance"
        result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "redis_instance_guard_mismatch"))
        self.assertEqual(self.restarted, [])
        self.assertEqual(self.backups(), [])

    def test_all_units_must_share_environment_and_network_namespace(self):
        unit = secure.UNITS[-1]
        for field, value in (("EnvironmentFiles", "other.env (ignore_errors=no)"),
                             ("PrivateNetwork", "yes"), ("NetworkNamespacePath", "/another"),
                             ("JoinsNamespaceOf", "another.service")):
            with self.subTest(field=field):
                previous = self.properties[unit][field]
                self.properties[unit][field] = value
                result, code = self.run_secure()
                self.assertEqual((code, result["reason"]), (1, "service_configuration_guard_mismatch"))
                self.properties[unit][field] = previous
        self.assertEqual(self.restarted, [])
        self.assertEqual(self.backups(), [])

    def test_restart_failure_stops_immediately_without_closing_port(self):
        self.restart_failure = secure.UNITS[1]
        result, code = self.run_secure()
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertEqual(self.restarted, [secure.UNITS[0]])
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)
        self.assertEqual(len(self.backups()), 2)

    def test_first_restart_failure_keeps_env_backup_and_leaves_bind_open(self):
        self.restart_failure = secure.UNITS[0]
        result, code = self.run_secure()
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertEqual(self.restarted, [])
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)
        self.assertEqual(len(self.backups()), 2)
        self.assertTrue((self.lock_dir / "owner").exists())

    def test_each_restart_must_pass_active_and_environment_checks(self):
        self.active_failure = secure.UNITS[1]
        result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "service_configuration_guard_mismatch"))
        self.assertEqual(self.restarted, list(secure.UNITS[:2]))
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)

    def test_lingering_public_client_prevents_persistent_or_runtime_bind_change(self):
        self.public_lingers = True
        result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "non_loopback_clients_present"))
        self.assertEqual(self.restarted, list(secure.UNITS))
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)

    def test_run_id_changing_during_restarts_prevents_all_bind_changes(self):
        original = self.command

        def restart_then_replace_instance(args, timeout=10):
            result = original(args, timeout)
            if args[-1] == secure.UNITS[-1]:
                self.local.run_id = "new-instance"
            return result

        with patch.object(secure, "command", side_effect=restart_then_replace_instance):
            result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "redis_instance_guard_mismatch"))
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)
        self.assertTrue((self.lock_dir / "owner").exists())

    def test_stale_service_environment_after_restart_stops_before_next_service(self):
        original = self.command

        def restart_with_old_environment(args, timeout=10):
            result = original(args, timeout)
            if args[-1] == secure.UNITS[1]:
                self.hosts[self.pid_by_unit[secure.UNITS[1]]] = PUBLIC
            return result

        with patch.object(secure, "command", side_effect=restart_with_old_environment):
            result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "service_environment_unverified"))
        self.assertEqual(self.restarted, list(secure.UNITS[:2]))
        self.assertEqual(self.local.commands, [])
        self.assertGreaterEqual(self.clock, 15)

    def test_restarted_unit_waits_for_initially_missing_environment_then_loopback(self):
        original = secure.process_host
        attempted = 0

        def transient_host(pid):
            nonlocal attempted
            if self.restarted == [secure.UNITS[0]] and pid == self.pid_by_unit[secure.UNITS[0]]:
                attempted += 1
                if attempted == 1:
                    raise secure.Refused("service_environment_unverified")
                if attempted == 2:
                    raise FileNotFoundError("transient-proc")
            return original(pid)

        with patch.object(secure, "process_host", side_effect=transient_host):
            result, code = self.run_secure()
        self.assertEqual((code, result["status"]), (0, "updated"))
        self.assertEqual(attempted, 3)
        self.assertEqual(self.restarted, list(secure.UNITS))
        self.assertEqual(self.clock, 0.2)
        self.assertEqual(self.local.commands, [("CONFIG", "SET", "bind", "127.0.0.1")])

    def test_env_already_loopback_can_safely_complete_remaining_migration(self):
        self.env_file.write_bytes(self.env_original.replace(PUBLIC.encode(), b"127.0.0.1"))
        self.values["REDIS_HOST"] = "127.0.0.1"
        self.public.closed = True
        self.hosts[self.pid_by_unit[secure.UNITS[0]]] = "127.0.0.1"
        self.expected_restarts = list(secure.UNITS[1:])
        result, code = self.run_secure()
        self.assertEqual((code, result["status"]), (0, "updated"))
        self.assertEqual(self.restarted, list(secure.UNITS[1:]))
        self.assertNotIn("restart:" + secure.UNITS[0], self.trace)
        self.assertEqual(self.local.commands, [("CONFIG", "SET", "bind", "127.0.0.1")])

    def test_already_secured_instance_performs_no_restarts_or_writes(self):
        self.env_file.write_bytes(self.env_original.replace(PUBLIC.encode(), b"127.0.0.1"))
        self.values["REDIS_HOST"] = "127.0.0.1"
        self.config_file.write_bytes(self.conf_original.replace(b"0.0.0.0", b"127.0.0.1"))
        self.local.bind = "127.0.0.1"
        self.public.closed = True
        self.hosts = {pid: "127.0.0.1" for pid in self.hosts}
        result, code = self.run_secure()
        self.assertEqual((code, result["status"]), (0, "nochange"))
        self.assertFalse(any(event.startswith("restart:") for event in self.trace))
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.backups(), [])

    def test_bind_failure_returns_error_keeps_evidence_and_does_not_reopen(self):
        self.bind_fails = True
        result, code = self.run_secure()
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertEqual(self.local.commands, [("CONFIG", "SET", "bind", "127.0.0.1")])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original.replace(b"0.0.0.0", b"127.0.0.1"))
        self.assertEqual(len(self.backups()), 2)
        self.assertTrue((self.lock_dir / "owner").exists())
        for private in ("private-password", "private-body", PUBLIC, str(self.env_file)):
            self.assertNotIn(private, json.dumps(result))

    def test_lock_change_after_backup_prevents_env_and_service_changes(self):
        original = secure.backups

        def backup_then_change(*args):
            original(*args)
            (self.lock_dir / "owner").write_text("another-owner\n")

        with patch.object(secure, "backups", side_effect=backup_then_change):
            result, code = self.run_secure()
        self.assertEqual(code, 1)
        self.assertEqual(self.env_file.read_bytes(), self.env_original)
        self.assertEqual(self.restarted, [])
        self.assertEqual(self.local.commands, [])

    def test_configuration_change_before_restart_is_refused_without_restart_retry(self):
        original = secure.backups

        def backup_then_change(*args):
            original(*args)
            self.properties[secure.UNITS[0]]["PrivateNetwork"] = "yes"

        with patch.object(secure, "backups", side_effect=backup_then_change):
            result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "service_configuration_guard_mismatch"))
        self.assertEqual(self.restarted, [])
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)
        self.assertTrue((self.lock_dir / "owner").exists())

    def test_duplicate_host_is_refused(self):
        self.env_file.write_bytes(self.env_original + b"REDIS_HOST=8.8.8.8\n")
        result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "redis_host_ambiguous"))
        self.assertEqual(self.restarted, [])

    def test_wrong_server_route_is_refused(self):
        self.env_file.write_bytes(self.env_original.replace(PUBLIC.encode(), b"9.9.9.9"))
        self.values["REDIS_HOST"] = "9.9.9.9"
        result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "redis_route_not_this_server"))
        self.assertEqual(self.restarted, [])

    def test_work_starting_between_restarts_stops_without_cancelling_it(self):
        self.busy_before_restart = 1
        result, code = self.run_secure()
        self.assertEqual((code, result["reason"]), (1, "running_work_present"))
        self.assertEqual(self.restarted, [secure.UNITS[0]])
        self.assertEqual(self.local.commands, [])
        self.assertEqual(self.config_file.read_bytes(), self.conf_original)
        self.assertTrue((self.lock_dir / "owner").exists())

    def test_readonly_guard_checks_both_tables_and_never_filters_pending_images(self):
        class Database:
            def __init__(self, counts):
                self.counts, self.statements = counts, []
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def transaction(self): return self
            def execute(self, sql):
                self.statements.append(sql)
                return self
            def fetchone(self): return self.counts
        for counts in ((0, 0), (1, 0), (0, 1)):
            with self.subTest(counts=counts):
                database = Database(counts)
                connect = unittest.mock.Mock(return_value=database)
                with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
                    if counts == (0, 0):
                        REAL_IDLE({"DATABASE_URL": "private-database"})
                    else:
                        with self.assertRaisesRegex(secure.Refused, "running_work_present"):
                            REAL_IDLE({"DATABASE_URL": "private-database"})
                connect.assert_called_once_with("private-database", connect_timeout=5)
                self.assertEqual(database.statements[0], "SET TRANSACTION READ ONLY")
                self.assertIn("FROM tasks WHERE status = 'running'", database.statements[1])
                self.assertIn("FROM scheduled_tasks WHERE status = 'running'", database.statements[1])
                self.assertNotIn("pending", database.statements[1])

    def test_health_requires_api_path_and_json_status_ok(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, _limit): return self.body
        with patch.object(secure.urllib.request, "urlopen", return_value=Response(b'{"status":"ok"}')) as urlopen:
            REAL_HEALTH()
        urlopen.assert_called_once_with("http://127.0.0.1:8000/api/health", timeout=2)
        with patch.object(secure.urllib.request, "urlopen", return_value=Response(b'{"status":"unhealthy"}')), \
                patch.object(secure.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(secure.Refused, "backend_health_failed"):
                REAL_HEALTH()

    def test_listener_verification_requires_only_loopback_and_external_connection_refusal(self):
        line = 'LISTEN 0 511 127.0.0.1:6379 0.0.0.0:* users:(("redis-server",pid=123,fd=6))\n'
        with patch.object(secure, "command", return_value=line), \
                patch.object(secure.socket, "create_connection", side_effect=ConnectionRefusedError()):
            REAL_LISTENERS({"process_id": 123}, PUBLIC)
        with patch.object(secure, "command", return_value=line.replace("127.0.0.1:6379", "0.0.0.0:6379")):
            with self.assertRaisesRegex(secure.Refused, "listener_verification_failed"):
                REAL_LISTENERS({"process_id": 123}, PUBLIC)
        with patch.object(secure, "command", return_value=line), \
                patch.object(secure.socket, "create_connection", return_value=unittest.mock.Mock()):
            with self.assertRaisesRegex(secure.Refused, "non_loopback_connection_still_open"):
                REAL_LISTENERS({"process_id": 123}, PUBLIC)


if __name__ == "__main__":
    unittest.main()
