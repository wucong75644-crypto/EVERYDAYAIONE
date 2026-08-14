#!/usr/bin/env python3
"""安全地将管理员数据库 URL 转换为 libpq 环境后启动 psql。"""

from __future__ import annotations

import os
import shutil
import sys
from urllib.parse import parse_qsl, unquote, urlparse


QUERY_ENV = {
    "application_name": "PGAPPNAME",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "options": "PGOPTIONS",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
}
LIBPQ_CONNECTION_ENV = {
    "PGAPPNAME",
    "PGCHANNELBINDING",
    "PGCLIENTENCODING",
    "PGCONNECT_TIMEOUT",
    "PGDATABASE",
    "PGHOST",
    "PGHOSTADDR",
    "PGOPTIONS",
    "PGPASSFILE",
    "PGPASSWORD",
    "PGPORT",
    "PGREQUIRESSL",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGSSLCERT",
    "PGSSLCRL",
    "PGSSLCRLDIR",
    "PGSSLKEY",
    "PGSSLMODE",
    "PGSSLROOTCERT",
    "PGTARGETSESSIONATTRS",
    "PGUSER",
}


def build_psql_environment(database_url: str) -> dict[str, str]:
    """解析受控 PostgreSQL URL，避免把凭证放入进程参数。"""
    if any(character.isspace() for character in database_url):
        raise ValueError("TENANT_DB_ADMIN_URL 不能包含空白字符")
    parsed = urlparse(database_url)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not parsed.username
        or not parsed.path.lstrip("/")
        or parsed.fragment
    ):
        raise ValueError("TENANT_DB_ADMIN_URL 不是完整 PostgreSQL URL")
    try:
        port = parsed.port or 5432
    except ValueError as exc:
        raise ValueError("TENANT_DB_ADMIN_URL 端口无效") from exc

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = [key for key, _ in query_pairs]
    if len(query_keys) != len(set(query_keys)):
        raise ValueError("TENANT_DB_ADMIN_URL 查询参数不能重复")
    unknown = sorted(set(query_keys) - set(QUERY_ENV))
    if unknown:
        raise ValueError("TENANT_DB_ADMIN_URL 包含不支持的查询参数")

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in LIBPQ_CONNECTION_ENV
    }
    environment.update({
        "PGHOST": unquote(parsed.hostname),
        "PGPORT": str(port),
        "PGDATABASE": unquote(parsed.path.lstrip("/")),
        "PGUSER": unquote(parsed.username),
    })
    password = unquote(parsed.password or "")
    if password:
        environment["PGPASSWORD"] = password
    for key, value in query_pairs:
        environment[QUERY_ENV[key]] = value
    return environment


def main() -> int:
    database_url = os.environ.get("TENANT_DB_ADMIN_URL", "")
    if not database_url:
        print("❌ 缺少 TENANT_DB_ADMIN_URL", file=sys.stderr)
        return 1
    if shutil.which("psql") is None:
        print("❌ 未找到 psql", file=sys.stderr)
        return 1
    try:
        environment = build_psql_environment(database_url)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    os.execvpe("psql", ["psql", *sys.argv[1:]], environment)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
