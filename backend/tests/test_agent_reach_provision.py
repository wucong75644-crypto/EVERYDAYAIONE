import importlib.util
import json
from pathlib import Path
import socket
import sys

import pytest

spec = importlib.util.spec_from_file_location('reach_provision', Path(__file__).parents[2]/'deploy/provision-agent-reach-xhs.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def test_plan_does_not_modify_env_or_require_binary(tmp_path, monkeypatch, capsys):
    env = tmp_path/'config'
    original = 'AGENT_REACH_XHS_SLOTS_JSON={}\nUNRELATED=value\n'
    env.write_text(original)
    monkeypatch.setattr(sys, 'argv', ['provision', '--org', '512c973e-6a07-4ce2-be89-8f5896bc88f7',
        '--binary', '/not-installed', '--cache', '/not-installed', '--env-file', str(env)])
    provision.main()
    assert env.read_text() == original
    assert json.loads(capsys.readouterr().out)['to_provision'] == 1
    assert list(tmp_path.iterdir()) == [env]


def test_invalid_org_rejected_before_configuration_read(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['provision', '--org', 'invalid', '--binary', '/x', '--cache', '/x'])
    with pytest.raises(ValueError):
        provision.main()


def test_port_reservation_excludes_busy_ports():
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0))
        held = []
        assert not provision.reserve(busy.getsockname()[1], held)
        assert not held
    held = []
    assert provision.reserve(0, held)
    held[0].close()


def test_atomic_write_preserves_content_and_restricts_mode(tmp_path):
    path = tmp_path/'secret'
    path.write_text('old')
    path.chmod(0o644)
    provision.atomic_write(path, 'new\n')
    assert path.read_text() == 'new\n'
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]
