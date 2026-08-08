"""Executable UNIX DAC preflight contracts for Model Gateway env files."""

from pathlib import Path

import pytest

from backend.tests.test_agent_runtime_flags_off_install import _run_installer


@pytest.mark.parametrize(
    ("extra_env", "expected"),
    (
        ({"FAKE_FILE_GID": "988"}, "root:everydayai-model-gateway-secret"),
        ({"FAKE_RUNTIME_SECRET_GROUP": "1"}, "Runtime user 禁止加入 Gateway secret group"),
    ),
)
def test_installer_rejects_gateway_env_dac_violation_before_writes(
    tmp_path: Path, extra_env: dict[str, str], expected: str,
) -> None:
    result, systemd_dir, calls = _run_installer(tmp_path, extra_env)

    assert result.returncode == 1
    assert expected in result.stderr
    assert not any(systemd_dir.iterdir())
    assert not calls.exists()
