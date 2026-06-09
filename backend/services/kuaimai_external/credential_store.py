"""
快麦 Web 凭证 CRUD（kuaimai_external_credentials 表）

职责：
  - 管理员粘贴 cURL 解析后，调 save_credential 写入
  - sync 任务调 get_credential 拿当前凭证
  - sync 失败时调 mark_expired 标记失效

多租户：所有方法都需传 org_id，操作前必须由调用方校验权限。
建议在 API endpoint 层用 OrgScopedDB 自动隔离，本模块按显式 org_id 操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from loguru import logger

from services.kuaimai_external import cookie_crypto


Source = Literal["thinktank", "viperp"]
Status = Literal["active", "expired", "invalid"]


@dataclass
class Credential:
    """凭证记录（业务读出用）"""
    id: str
    org_id: str
    source: str
    kuaimai_company_id: int
    censeid_cookie: str
    cookie_full: str | None
    status: str
    last_health_check_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    created_at: datetime
    updated_at: datetime


def _row_to_credential(row: dict) -> Credential:
    """从 DB 行构造 Credential — 自动解密 cookie 字段。"""
    org_id = row["org_id"]
    return Credential(
        id=row["id"],
        org_id=org_id,
        source=row["source"],
        kuaimai_company_id=row["kuaimai_company_id"],
        censeid_cookie=cookie_crypto.decrypt_cookie(
            org_id=org_id, stored=row["censeid_cookie"]
        ),
        cookie_full=cookie_crypto.decrypt_cookie(
            org_id=org_id, stored=row.get("cookie_full") or ""
        ) or None,
        status=row["status"],
        last_health_check_at=row.get("last_health_check_at"),
        last_sync_at=row.get("last_sync_at"),
        last_sync_status=row.get("last_sync_status"),
        last_sync_error=row.get("last_sync_error"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ──────────────────────── 写操作 ────────────────────────


def save_credential(
    db: Any,
    *,
    org_id: str,
    source: Source,
    kuaimai_company_id: int,
    censeid_cookie: str,
    cookie_full: str | None = None,
) -> str:
    """
    保存凭证（如果该 org+source 已存在则覆盖更新，否则插入）。

    Returns:
        凭证 id

    设计：用 UPSERT 模式，保证 (org_id, source) 唯一约束语义。
    """
    if not censeid_cookie:
        raise ValueError("censeid_cookie 不能为空")
    if not kuaimai_company_id:
        raise ValueError("kuaimai_company_id 不能为空")

    # 加密敏感字段（cookie）
    payload = {
        "org_id": org_id,
        "source": source,
        "kuaimai_company_id": kuaimai_company_id,
        "censeid_cookie": cookie_crypto.encrypt_cookie(
            org_id=org_id, plaintext=censeid_cookie
        ),
        "cookie_full": cookie_crypto.encrypt_cookie(
            org_id=org_id, plaintext=cookie_full or ""
        ) if cookie_full else None,
        "status": "active",
        # 上传新 cookie = 视为已通过健康检查（调用方应该已经测过连接）
        "last_health_check_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    resp = (
        db.table("kuaimai_external_credentials")
        .upsert(payload, on_conflict="org_id,source")
        .execute()
    )
    if not resp.data:
        raise RuntimeError("save_credential 返回空，UPSERT 失败")

    cred_id = resp.data[0]["id"]
    logger.info(
        f"KuaimaiExternal credential saved | "
        f"org={org_id} source={source} companyid={kuaimai_company_id} id={cred_id}"
    )
    return cred_id


def mark_expired(db: Any, *, credential_id: str, error_msg: str) -> None:
    """
    标记凭证为 expired（cookie 失效后调）。

    后续 sync 任务会跳过 status != 'active' 的凭证。
    管理员前端会看到红字提醒重新粘贴 cURL。
    """
    db.table("kuaimai_external_credentials").update({
        "status": "expired",
        "last_sync_status": "failed",
        "last_sync_error": error_msg[:500],  # 截断防爆字段
        "updated_at": datetime.now().isoformat(),
    }).eq("id", credential_id).execute()
    logger.warning(
        f"KuaimaiExternal credential marked expired | "
        f"id={credential_id} | err={error_msg[:100]}"
    )


def record_sync_success(
    db: Any,
    *,
    credential_id: str,
) -> None:
    """记录一次成功的同步（更新 last_sync_at）。"""
    now = datetime.now().isoformat()
    db.table("kuaimai_external_credentials").update({
        "last_sync_at": now,
        "last_sync_status": "success",
        "last_sync_error": None,
        "last_health_check_at": now,
        "status": "active",  # 同步成功 = cookie 还在生效
        "updated_at": now,
    }).eq("id", credential_id).execute()


def record_sync_failure(
    db: Any,
    *,
    credential_id: str,
    error_msg: str,
) -> None:
    """记录一次失败的同步（不一定是 cookie 失效，可能是临时网络错误）。"""
    db.table("kuaimai_external_credentials").update({
        "last_sync_at": datetime.now().isoformat(),
        "last_sync_status": "failed",
        "last_sync_error": error_msg[:500],
        "updated_at": datetime.now().isoformat(),
    }).eq("id", credential_id).execute()


def delete_credential(db: Any, *, credential_id: str, org_id: str) -> bool:
    """
    删除凭证（管理员主动删除）。

    带 org_id 二次校验，防止跨 org 误删（即使 OrgScopedDB 漏隔离）。
    """
    resp = (
        db.table("kuaimai_external_credentials")
        .delete()
        .eq("id", credential_id)
        .eq("org_id", org_id)
        .execute()
    )
    deleted = bool(resp.data)
    if deleted:
        logger.info(f"KuaimaiExternal credential deleted | id={credential_id}")
    return deleted


# ──────────────────────── 读操作 ────────────────────────


def get_credential(db: Any, *, org_id: str, source: Source) -> Credential | None:
    """根据 org_id + source 拿凭证（不校验 status，调用方自行判断）"""
    resp = (
        db.table("kuaimai_external_credentials")
        .select("*")
        .eq("org_id", org_id)
        .eq("source", source)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _row_to_credential(resp.data[0])


def get_active_credential(db: Any, *, org_id: str, source: Source) -> Credential | None:
    """只拿 active 的（sync 任务用，跳过已过期的）。"""
    resp = (
        db.table("kuaimai_external_credentials")
        .select("*")
        .eq("org_id", org_id)
        .eq("source", source)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _row_to_credential(resp.data[0])


def list_credentials(db: Any, *, org_id: str) -> list[Credential]:
    """列出该 org 下所有凭证（前端配置页展示）。"""
    resp = (
        db.table("kuaimai_external_credentials")
        .select("*")
        .eq("org_id", org_id)
        .order("source")
        .execute()
    )
    return [_row_to_credential(r) for r in (resp.data or [])]


def list_all_active_credentials(db: Any) -> list[Credential]:
    """跨 org 列出所有 active 凭证（定时任务用，遍历所有企业同步）。"""
    resp = (
        db.table("kuaimai_external_credentials")
        .select("*")
        .eq("status", "active")
        .execute()
    )
    return [_row_to_credential(r) for r in (resp.data or [])]
