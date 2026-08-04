"""C6.2-B2 contracts for the isolated Agent Runtime process settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime_worker_main import (
    AgentRuntimeProcessSettings,
    AuthorizationProcessSettings,
    ProjectionProcessSettings,
    SandboxProcessSettings,
)


ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy/everydayai-agent-runtime.service"


def test_agent_runtime_settings_field_allowlist() -> None:
    assert set(AgentRuntimeProcessSettings.model_fields) == {
        "worker_database_url",
        "agent_runtime_process_role",
        "agent_runtime_worker_id",
        "agent_runtime_release_revision",
        "agent_runtime_health_socket",
        "agent_runtime_poll_interval_seconds",
        "agent_runtime_heartbeat_seconds",
        "agent_runtime_drain_timeout_seconds",
        "agent_runtime_production_composition_enabled",
        "agent_runtime_agent_definition_id",
        "agent_runtime_agent_definition_revision",
        "sandbox_job_root",
        "sandbox_runtime_revision",
    }


def test_runtime_does_not_load_dotenv_but_backend_settings_still_does(
    tmp_path: Path,
) -> None:
    marker = "fake-provider-secret-from-dotenv"
    (tmp_path / ".env").write_text(
        "\n".join((
            "DATABASE_URL=postgresql://backend@127.0.0.1/backend",
            "JWT_SECRET_KEY=backend-test-jwt",
            f"KIE_API_KEY={marker}",
            "GOOGLE_API_KEY=fake-google",
            "DASHSCOPE_API_KEY=fake-dashscope",
            "APP_OPENROUTER_API_KEY=fake-openrouter",
            "CONFIG_KEK=fake-kek",
            "OSS_ACCESS_KEY_SECRET=fake-oss",
        )),
    )
    script = """
import json
import agent_runtime_worker_main as entrypoint
from services.agent.runtime.infrastructure.model.runtime_adapter_factory import (
    create_runtime_chat_adapter,
)

runtime = entrypoint.AgentRuntimeProcessSettings()
runtime_dump = runtime.model_dump()
adapter = create_runtime_chat_adapter(
    "gemini-3-flash", api_key="lease-material", stream_timeout=1.0,
)
from core.config import get_settings
runtime_called_common_settings = get_settings.cache_info().misses != 0

backend = get_settings()
print(json.dumps({
    "runtime_called_common_settings": runtime_called_common_settings,
    "runtime_production_enabled": runtime.agent_runtime_production_composition_enabled,
    "runtime_fields": sorted(runtime_dump),
    "runtime_values": list(runtime_dump.values()),
    "runtime_adapter_created": type(adapter).__name__ == "KieChatAdapter",
    "backend_kie_loaded": backend.kie_api_key == "fake-provider-secret-from-dotenv",
}))
"""
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            "DATABASE_URL", "JWT_SECRET_KEY", "KIE_API_KEY", "GOOGLE_API_KEY",
            "DASHSCOPE_API_KEY", "APP_OPENROUTER_API_KEY", "CONFIG_KEK",
            "OSS_ACCESS_KEY_SECRET",
        }
    }
    environment.update({
        "PYTHONPATH": str(ROOT / "backend"),
        "WORKER_DATABASE_URL": "postgresql://runtime@127.0.0.1/runtime",
        "AGENT_RUNTIME_PROCESS_ROLE": "agent_runtime",
        "AGENT_RUNTIME_WORKER_ID": "runtime-test",
        "AGENT_RUNTIME_RELEASE_REVISION": "test-revision",
        "AGENT_RUNTIME_HEALTH_SOCKET": str(tmp_path / "health.sock"),
        "SANDBOX_JOB_ROOT": str(tmp_path / "jobs"),
        "SANDBOX_RUNTIME_REVISION": "sandbox-test",
    })
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=environment,
        check=True, capture_output=True, text=True,
    )
    result = json.loads(completed.stdout)

    assert result["runtime_called_common_settings"] is False
    assert result["runtime_adapter_created"] is True
    assert result["runtime_production_enabled"] is False
    assert marker not in result["runtime_values"]
    assert result["runtime_fields"] == sorted(
        AgentRuntimeProcessSettings.model_fields,
    )
    assert result["backend_kie_loaded"] is True


def test_runtime_systemd_blocks_current_and_legacy_dotenv_paths() -> None:
    unit = UNIT.read_text()
    assert (
        "InaccessiblePaths=-/var/www/everydayai/backend/.env "
        "-/var/www/everydayai/backend/.env.kek "
        "-/etc/everydayai/agent-runtime-model.env"
    ) in unit


COMMON_FIELDS = {
    "worker_database_url",
    "agent_runtime_process_role",
    "agent_runtime_worker_id",
    "agent_runtime_release_revision",
    "agent_runtime_health_socket",
    "agent_runtime_poll_interval_seconds",
    "agent_runtime_heartbeat_seconds",
    "sentry_dsn",
    "environment",
}


def test_projection_and_authorization_field_allowlists() -> None:
    assert set(AuthorizationProcessSettings.model_fields) == COMMON_FIELDS
    assert set(ProjectionProcessSettings.model_fields) == COMMON_FIELDS | {
        "redis_host", "redis_port", "redis_password", "redis_db", "redis_ssl",
    }
    forbidden = {
        "kie_api_key", "google_api_key", "dashscope_api_key",
        "openrouter_api_key", "config_kek", "jwt_secret_key",
        "oss_access_key_secret",
    }
    for settings_type in (
        AgentRuntimeProcessSettings, ProjectionProcessSettings,
        AuthorizationProcessSettings, SandboxProcessSettings,
    ):
        assert forbidden.isdisjoint(settings_type.model_fields)


@pytest.mark.parametrize(
    "role", ("agent_runtime", "projection", "authorization", "sandbox"),
)
def test_each_worker_role_ignores_application_dotenv(
    tmp_path: Path, role: str,
) -> None:
    marker = "forbidden-provider-material"
    (tmp_path / ".env").write_text("\n".join((
        "DATABASE_URL=postgresql://backend@127.0.0.1/backend",
        "JWT_SECRET_KEY=fake-jwt",
        f"KIE_API_KEY={marker}",
        "CONFIG_KEK=fake-kek",
        "OSS_ACCESS_KEY_SECRET=fake-oss",
    )))
    script = """
import json
import agent_runtime_worker_main as entrypoint
from core.config import get_settings

role = __import__('os').environ['AGENT_RUNTIME_PROCESS_ROLE']
settings = entrypoint._load_process_settings(role)
before = get_settings.cache_info().misses
rendered = repr(settings)
backend = get_settings()
print(json.dumps({
    'before': before,
    'fields': sorted(settings.model_fields),
    'rendered': rendered,
    'backend_kie': backend.kie_api_key,
}))
"""
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            "DATABASE_URL", "JWT_SECRET_KEY", "KIE_API_KEY", "CONFIG_KEK",
            "OSS_ACCESS_KEY_SECRET",
        }
    }
    environment.update({
        "PYTHONPATH": str(ROOT / "backend"),
        "WORKER_DATABASE_URL": "postgresql://worker@127.0.0.1/runtime",
        "AGENT_RUNTIME_PROCESS_ROLE": role,
        "AGENT_RUNTIME_WORKER_ID": f"{role}-test",
        "AGENT_RUNTIME_RELEASE_REVISION": "test-revision",
        "AGENT_RUNTIME_HEALTH_SOCKET": str(tmp_path / f"{role}.sock"),
        "SANDBOX_JOB_ROOT": str(tmp_path / "jobs"),
        "SANDBOX_RUNTIME_REVISION": "sandbox-test",
        "SANDBOX_ROOTFS": str(tmp_path / "rootfs"),
        "SANDBOX_ROOTFS_MANIFEST": str(tmp_path / "manifest"),
        "SANDBOX_ROOTFS_SHA256": "0" * 64,
        "SANDBOX_NSJAIL_PATH": "/usr/bin/nsjail",
        "SANDBOX_NSJAIL_SHA256": "1" * 64,
        "SANDBOX_PYTHON_PATH": "/usr/bin/python3.12",
        "SANDBOX_SECCOMP_POLICY": str(tmp_path / "sandbox.policy"),
        "SANDBOX_SECCOMP_SHA256": "2" * 64,
        "SANDBOX_CGROUP_V2_MOUNT": "/sys/fs/cgroup",
        "REDIS_PASSWORD": "projection-password-do-not-render",
    })
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=environment,
        check=True, capture_output=True, text=True,
    )
    result = json.loads(completed.stdout)
    assert result["before"] == 0
    assert marker not in result["rendered"]
    assert "projection-password-do-not-render" not in result["rendered"]
    assert result["backend_kie"] == marker


def test_all_worker_units_block_application_secret_files() -> None:
    inaccessible = (
        "InaccessiblePaths=-/var/www/everydayai/backend/.env "
        "-/var/www/everydayai/backend/.env.kek "
        "-/etc/everydayai/agent-runtime-model.env"
    )
    for name in (
        "everydayai-agent-runtime.service",
        "everydayai-agent-projection.service",
        "everydayai-agent-authorization.service",
        "everydayai-sandbox-worker.service",
    ):
        assert inaccessible in (ROOT / "deploy" / name).read_text()


def test_projection_and_authorization_templates_are_narrow() -> None:
    templates = ROOT / "deploy" / "env-templates"
    projection = (templates / "agent-projection-worker.env.template").read_text()
    authorization = (
        templates / "agent-authorization-worker.env.template"
    ).read_text()
    for forbidden in (
        "KIE_", "GOOGLE_", "DASHSCOPE_", "OPENROUTER_", "CONFIG_KEK",
        "JWT_", "OSS_",
    ):
        assert forbidden not in projection
        assert forbidden not in authorization
    assert "REDIS_HOST=" in projection
    assert "REDIS_PASSWORD=" in projection
    assert "REDIS_" not in authorization
