"""Read approved production sources and render flags-off control-plane envs."""
from __future__ import annotations

import ast
import base64
import json
from pathlib import Path
import re
import stat
from urllib.parse import quote, unquote, urlsplit, urlunsplit

ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PASSWORD_KEYS = {
    "agent-runtime-worker.env": "EVERYDAYAI_AGENT_RUNTIME_WORKER_PASSWORD",
    "agent-projection-worker.env": "EVERYDAYAI_PROJECTION_WORKER_PASSWORD",
    "agent-authorization-worker.env": "EVERYDAYAI_AUTHORIZATION_WORKER_PASSWORD",
}
ROLES = {
    "agent-runtime-worker.env": "everydayai_agent_runtime_worker",
    "agent-projection-worker.env": "everydayai_projection_worker",
    "agent-authorization-worker.env": "everydayai_authorization_worker",
}


class ProvisioningError(RuntimeError):
    """A redacted provisioning contract failure."""


def _parse_quoted_value(raw: str, quote_char: str) -> str:
    escaped = False
    for index in range(1, len(raw)):
        character = raw[index]
        if quote_char == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character == quote_char and not escaped:
            suffix = raw[index + 1:].strip()
            if suffix and not suffix.startswith("#"):
                raise ProvisioningError("环境文件包含无效的引号后内容")
            token = raw[:index + 1]
            if quote_char == "'":
                return token[1:-1]
            value = ast.literal_eval(token)
            if not isinstance(value, str):
                raise ProvisioningError("环境文件包含非字符串配置值")
            return value
        escaped = False
    raise ProvisioningError("环境文件包含未闭合引号")


def _parse_env_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in {"'", '"'}:
        return _parse_quoted_value(raw, raw[0])
    comment = re.search(r"[ \t]+#", raw)
    return raw[:comment.start()].rstrip() if comment else raw.rstrip()


def read_required_values(
    path: Path, required: set[str], allowed_modes: set[int], *, exact: bool = False,
) -> dict[str, str]:
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise ProvisioningError(f"缺少安全配置文件: {path}") from exc
    if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
        raise ProvisioningError(f"安全配置路径必须是普通文件: {path}")
    if stat.S_IMODE(file_stat.st_mode) not in allowed_modes:
        allowed = "/".join(f"{mode:04o}" for mode in sorted(allowed_modes))
        raise ProvisioningError(f"安全配置文件权限必须为 {allowed}: {path}")
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProvisioningError(f"无法读取安全配置文件: {path}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise ProvisioningError(f"安全配置文件包含无效配置行: {path}")
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise ProvisioningError(f"安全配置文件包含无效配置键: {path}")
        if key not in required:
            if exact:
                raise ProvisioningError(f"安全配置文件包含未批准键: {path}")
            continue
        if key in values:
            raise ProvisioningError(f"安全配置文件包含重复必需键: {key}")
        value = _parse_env_value(raw_value)
        if "\n" in value or "\r" in value:
            raise ProvisioningError(f"安全配置值不能包含换行符: {key}")
        values[key] = value
    missing = sorted(required - values.keys())
    if missing:
        raise ProvisioningError(f"安全配置文件缺少必需键: {', '.join(missing)}")
    return values


def _worker_dsn(migrator_dsn: str, role: str, password: str) -> str:
    try:
        parts = urlsplit(migrator_dsn)
        port = parts.port
    except ValueError as exc:
        raise ProvisioningError("MIGRATION_DATABASE_URL 无效") from exc
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ProvisioningError("MIGRATION_DATABASE_URL 必须使用 PostgreSQL")
    if unquote(parts.username or "") != "everydayai_migrator":
        raise ProvisioningError("MIGRATION_DATABASE_URL 必须使用 migrator 角色")
    if not parts.password or not parts.hostname or not parts.path.strip("/"):
        raise ProvisioningError("MIGRATION_DATABASE_URL 缺少凭证、主机或数据库名")
    if parts.fragment or (port is not None and not 1 <= port <= 65535):
        raise ProvisioningError("MIGRATION_DATABASE_URL 边界无效")
    _, separator, host_port = parts.netloc.rpartition("@")
    if not separator or not host_port:
        raise ProvisioningError("MIGRATION_DATABASE_URL 缺少 userinfo")
    userinfo = f"{quote(role, safe='')}:{quote(password, safe='')}"
    return urlunsplit((parts.scheme, f"{userinfo}@{host_port}", parts.path, parts.query, ""))


def validate_kek(values: dict[str, str]) -> dict[str, str]:
    version = values["CONFIG_KEK_CURRENT_VERSION"]
    raw_keyring = values["CONFIG_KEK_KEYRING_JSON"]
    try:
        keyring = json.loads(raw_keyring)
        if not isinstance(keyring, dict) or version not in keyring:
            raise ValueError
        decoded = {
            key: base64.b64decode(value, validate=True)
            for key, value in keyring.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ProvisioningError("Runtime model KEK 配置无效") from None
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version) \
        or set(decoded) != set(keyring) \
        or any(not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key)
               or len(value) != 32 for key, value in decoded.items()) \
        or "'" in raw_keyring or "<" in raw_keyring:
        raise ProvisioningError("Runtime model KEK 配置无效")
    return values


def render_envs(
    backend: dict[str, str], migrator_dsn: str, release_sha: str,
    kek: dict[str, str],
) -> dict[str, str]:
    passwords = {name: backend[key] for name, key in PASSWORD_KEYS.items()}
    if any(len(value) < 24 or "\n" in value or "\r" in value
           for value in passwords.values()):
        raise ProvisioningError("control-plane 数据库密码不符合合同")
    urls = {name: _worker_dsn(migrator_dsn, ROLES[name], value)
            for name, value in passwords.items()}
    required = {"REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_SSL", "ENVIRONMENT"}
    if any(not backend[key] for key in required):
        raise ProvisioningError("现有 Redis/Environment 配置不能为空")
    shared = {key: backend[key] for key in ("SENTRY_DSN", "ENVIRONMENT")}
    projection = {key: backend[key] for key in
                  ("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_SSL")}
    rendered = {
        "agent-runtime-worker.env": {
            "WORKER_DATABASE_URL": urls["agent-runtime-worker.env"],
            "AGENT_RUNTIME_PROCESS_ROLE": "agent_runtime", "AGENT_RUNTIME_WORKER_ID": "agent-runtime-01",
            "AGENT_RUNTIME_RELEASE_REVISION": release_sha,
            "AGENT_RUNTIME_HEALTH_SOCKET": "/run/everydayai-agent-runtime/health.sock",
            "AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED": "false",
            "AGENT_RUNTIME_MEDIA_ENABLED": "false",
            "AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED": "false",
            "AGENT_RUNTIME_MEDIA_PRODUCTION_READY": "false",
            "SANDBOX_JOB_ROOT": "/var/lib/everydayai/sandbox-jobs", "SANDBOX_RUNTIME_REVISION": "unprovisioned",
        },
        "agent-projection-worker.env": {
            "WORKER_DATABASE_URL": urls["agent-projection-worker.env"], **projection,
            "AGENT_RUNTIME_PROCESS_ROLE": "projection", "AGENT_RUNTIME_WORKER_ID": "agent-projection-01",
            "AGENT_RUNTIME_RELEASE_REVISION": release_sha,
            "AGENT_RUNTIME_HEALTH_SOCKET": "/run/everydayai-agent-projection/health.sock",
            "AGENT_RUNTIME_MEDIA_ENABLED": "false",
            "AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED": "false",
            "MEDIA_WORKSPACE_ROOT": "/mnt/nas-workspace",
            "MEDIA_CDN_DOMAIN": "",
            "MEDIA_RESULT_ALLOWED_HOSTS": "",
            "AGENT_RUNTIME_POLL_INTERVAL_SECONDS": "1", "AGENT_RUNTIME_HEARTBEAT_SECONDS": "10", **shared,
        },
        "agent-authorization-worker.env": {
            "WORKER_DATABASE_URL": urls["agent-authorization-worker.env"],
            "AGENT_RUNTIME_PROCESS_ROLE": "authorization", "AGENT_RUNTIME_WORKER_ID": "agent-authorization-01",
            "AGENT_RUNTIME_RELEASE_REVISION": release_sha,
            "AGENT_RUNTIME_HEALTH_SOCKET": "/run/everydayai-agent-authorization/health.sock",
            "AGENT_RUNTIME_POLL_INTERVAL_SECONDS": "1", "AGENT_RUNTIME_HEARTBEAT_SECONDS": "10", **shared,
        },
    }
    output = {name: "".join(f"{key}={value}\n" for key, value in values.items())
              for name, values in rendered.items()}
    output["agent-runtime-model.env"] = (
        f"CONFIG_KEK_CURRENT_VERSION={kek['CONFIG_KEK_CURRENT_VERSION']}\n"
        f"CONFIG_KEK_KEYRING_JSON='{kek['CONFIG_KEK_KEYRING_JSON']}'\n"
    )
    return output
