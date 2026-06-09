"""
店铺-运营同步逻辑

从 viperp 响应中提取 (shop_user_id, shop_name, platform, operator_name)，
维护 erp_shop_operators + erp_operators 两张表 + 推送变化告警。

详细场景见 migrations/114 表注释 + 设计讨论：
  - 新店铺出现 → INSERT erp_shop_operators
  - 店铺归属变化（换运营，旧运营还在）→ UPDATE + audit + 告警
  - 店铺消失 → is_active=FALSE
  - 新运营 → 自动匹配企微 + INSERT erp_operators + audit + 告警
  - 已绑定运营的企微账号失效 → 自动解绑 + 告警
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from services.kuaimai_external import operator_resolver


# ──────────────────────── 数据结构 ────────────────────────


@dataclass
class ShopFromResponse:
    """从 viperp 响应一行中提取的店铺-运营信息"""
    shop_user_id: int
    shop_name: str
    platform_code: str | None
    platform_name: str | None
    taobao_id: int | None
    operator_name: str | None


@dataclass
class SyncChanges:
    """这次 sync 检测到的变化（用于汇总告警）"""
    new_shops: list[dict] = field(default_factory=list)
    operator_changes: list[dict] = field(default_factory=list)
    removed_shops: list[dict] = field(default_factory=list)
    new_operators_auto_bound: list[dict] = field(default_factory=list)
    new_operators_unbound: list[dict] = field(default_factory=list)
    binding_invalidated: list[dict] = field(default_factory=list)

    @property
    def has_any(self) -> bool:
        return any([
            self.new_shops, self.operator_changes, self.removed_shops,
            self.new_operators_auto_bound, self.new_operators_unbound,
            self.binding_invalidated,
        ])


# ──────────────────────── 提取 ────────────────────────


def extract_shop_from_row(row: dict) -> ShopFromResponse | None:
    """从 viperp list 接口一行响应提取店铺-运营信息。"""
    shop_user_id = row.get("userId")
    if not shop_user_id:
        return None
    try:
        shop_user_id = int(shop_user_id)
    except (ValueError, TypeError):
        return None

    return ShopFromResponse(
        shop_user_id=shop_user_id,
        shop_name=str(
            row.get("shopTitle") or row.get("shopNameWhole") or ""
        ).strip(),
        platform_code=str(row.get("shopSource") or "").strip() or None,
        platform_name=str(row.get("shopSourceName") or "").strip() or None,
        taobao_id=_try_int(row.get("taobaoId")),
        operator_name=str(row.get("shopGroupName") or "").strip() or None,
    )


def _try_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ──────────────────────── 店铺同步 ────────────────────────


def _sync_shop_row(
    db: Any,
    *,
    org_id: str,
    kuaimai_company_id: int,
    shop: ShopFromResponse,
    sync_batch_id: str,
    changes: SyncChanges,
) -> None:
    """UPSERT 单个店铺，检测变化记到 changes。"""
    existing_resp = (
        db.table("erp_shop_operators")
        .select("id, operator_name, is_active")
        .eq("org_id", org_id)
        .eq("kuaimai_company_id", kuaimai_company_id)
        .eq("shop_user_id", shop.shop_user_id)
        .limit(1)
        .execute()
    )
    existing = (existing_resp.data or [None])[0]

    now = datetime.now().isoformat()
    payload = {
        "org_id": org_id,
        "kuaimai_company_id": kuaimai_company_id,
        "shop_user_id": shop.shop_user_id,
        "shop_name": shop.shop_name,
        "platform_code": shop.platform_code,
        "platform_name": shop.platform_name,
        "taobao_id": shop.taobao_id,
        "operator_name": shop.operator_name,
        "last_seen_in_sync": sync_batch_id,
        "last_seen_at": now,
        "is_active": True,
        "updated_at": now,
    }

    if existing is None:
        # 新店铺
        db.table("erp_shop_operators").insert(payload).execute()
        changes.new_shops.append({
            "shop_user_id": shop.shop_user_id,
            "shop_name": shop.shop_name,
            "platform": shop.platform_name,
            "operator_name": shop.operator_name,
        })
        logger.info(
            f"shop_operator_sync 新店铺 | "
            f"shop_user_id={shop.shop_user_id} name={shop.shop_name} "
            f"operator={shop.operator_name}"
        )
        return

    # 已存在：检测运营变化 / 重新激活
    old_operator = existing.get("operator_name")
    if old_operator != shop.operator_name:
        changes.operator_changes.append({
            "shop_user_id": shop.shop_user_id,
            "shop_name": shop.shop_name,
            "platform": shop.platform_name,
            "old_operator": old_operator,
            "new_operator": shop.operator_name,
        })
        logger.info(
            f"shop_operator_sync 运营变化 | "
            f"shop={shop.shop_name} {old_operator} → {shop.operator_name}"
        )

    db.table("erp_shop_operators").update(payload).eq("id", existing["id"]).execute()


def _mark_removed_shops(
    db: Any,
    *,
    org_id: str,
    kuaimai_company_id: int,
    seen_shop_ids: set[int],
    changes: SyncChanges,
) -> None:
    """检测这次 sync 不再出现的店铺，标 is_active=FALSE。"""
    # 拿当前所有 active 店铺
    resp = (
        db.table("erp_shop_operators")
        .select("id, shop_user_id, shop_name, operator_name, platform_name")
        .eq("org_id", org_id)
        .eq("kuaimai_company_id", kuaimai_company_id)
        .eq("is_active", True)
        .execute()
    )
    for row in resp.data or []:
        if row["shop_user_id"] in seen_shop_ids:
            continue
        # 这次没看到 → 标 inactive
        db.table("erp_shop_operators").update({
            "is_active": False,
            "updated_at": datetime.now().isoformat(),
        }).eq("id", row["id"]).execute()
        changes.removed_shops.append({
            "shop_user_id": row["shop_user_id"],
            "shop_name": row["shop_name"],
            "platform": row.get("platform_name"),
            "last_operator": row.get("operator_name"),
        })
        logger.info(
            f"shop_operator_sync 店铺消失 | "
            f"shop_user_id={row['shop_user_id']} name={row['shop_name']}"
        )


# ──────────────────────── 运营同步 ────────────────────────


def _sync_operators(
    db: Any,
    *,
    org_id: str,
    operator_names: set[str],
    changes: SyncChanges,
) -> None:
    """
    遍历这次 sync 出现的所有运营名：
      - 已在 erp_operators → 更新 last_seen_at
      - 不在 → INSERT + 尝试自动匹配企微 + 记 changes
    """
    now = datetime.now().isoformat()

    for name in operator_names:
        if not name:
            continue

        existing_resp = (
            db.table("erp_operators")
            .select("id, is_bound, wecom_userid")
            .eq("org_id", org_id)
            .eq("operator_name", name)
            .limit(1)
            .execute()
        )
        existing = (existing_resp.data or [None])[0]

        if existing:
            # 已存在 → 只更新 last_seen
            db.table("erp_operators").update({
                "last_seen_at": now,
                "is_active": True,
                "updated_at": now,
            }).eq("id", existing["id"]).execute()
            continue

        # 新运营 → 尝试自动匹配企微
        match = operator_resolver.resolve_operator(
            db, org_id=org_id, operator_name=name
        )
        if match.status == "matched":
            # 自动绑定
            db.table("erp_operators").insert({
                "org_id": org_id,
                "operator_name": name,
                "wecom_userid": match.wecom_userid,
                "is_bound": True,
                "is_active": True,
                "first_seen_at": now,
                "last_seen_at": now,
                "bound_at": now,
                "bound_by": None,  # None = 系统自动
                "notes": "系统自动按姓名匹配",
            }).execute()
            changes.new_operators_auto_bound.append({
                "operator_name": name,
                "wecom_userid": match.wecom_userid,
            })
            logger.info(
                f"shop_operator_sync 新运营自动绑定 | "
                f"name={name} wecom_userid={match.wecom_userid}"
            )
        else:
            # 没匹配到 / 多匹配 → INSERT 未绑定
            db.table("erp_operators").insert({
                "org_id": org_id,
                "operator_name": name,
                "wecom_userid": None,
                "is_bound": False,
                "is_active": True,
                "first_seen_at": now,
                "last_seen_at": now,
                "notes": (
                    f"待管理员手动绑定（{match.status}, "
                    f"matched={match.matched_count}）"
                ),
            }).execute()
            changes.new_operators_unbound.append({
                "operator_name": name,
                "reason": match.status,
                "matched_count": match.matched_count,
            })
            logger.info(
                f"shop_operator_sync 新运营未绑定 | "
                f"name={name} reason={match.status}"
            )


def _verify_existing_bindings(
    db: Any,
    *,
    org_id: str,
    changes: SyncChanges,
) -> None:
    """
    自愈检查：找出所有 is_bound=TRUE 的运营，验证 wecom_userid 是否还在职。
    失效 → 自动解绑 + 记 changes（推告警）。
    """
    bound = (
        db.table("erp_operators")
        .select("id, operator_name, wecom_userid")
        .eq("org_id", org_id)
        .eq("is_bound", True)
        .eq("is_active", True)
        .execute()
    )
    for row in bound.data or []:
        wecom_userid = row.get("wecom_userid")
        if not wecom_userid:
            continue
        if operator_resolver.verify_binding_still_valid(
            db, org_id=org_id, wecom_userid=wecom_userid
        ):
            continue
        # 企微账号已失效 → 自动解绑
        db.table("erp_operators").update({
            "is_bound": False,
            "wecom_userid": None,
            "notes": (
                f"企微账号 {wecom_userid} 失效，"
                f"自动解绑于 {datetime.now().isoformat()}"
            ),
            "updated_at": datetime.now().isoformat(),
        }).eq("id", row["id"]).execute()
        changes.binding_invalidated.append({
            "operator_name": row["operator_name"],
            "stale_wecom_userid": wecom_userid,
        })
        logger.warning(
            f"shop_operator_sync 绑定失效自动解绑 | "
            f"operator={row['operator_name']} stale={wecom_userid}"
        )


# ──────────────────────── audit 写入 ────────────────────────


def _write_audit_records(
    db: Any,
    *,
    org_id: str,
    source: str,
    changes: SyncChanges,
    sync_batch_id: str,
) -> None:
    """把 changes 分类写入 kuaimai_field_audit 表（status=new 触发管理员关注）。"""
    records = []

    if changes.new_shops:
        records.append({
            "audit_type": "shop_added",
            "changes": {"items": changes.new_shops, "count": len(changes.new_shops)},
        })

    if changes.operator_changes:
        records.append({
            "audit_type": "operator_change",
            "changes": {
                "items": changes.operator_changes,
                "count": len(changes.operator_changes),
            },
        })

    if changes.removed_shops:
        records.append({
            "audit_type": "shop_removed",
            "changes": {
                "items": changes.removed_shops,
                "count": len(changes.removed_shops),
            },
        })

    if changes.new_operators_auto_bound or changes.new_operators_unbound:
        records.append({
            "audit_type": "new_operator",
            "changes": {
                "auto_bound": changes.new_operators_auto_bound,
                "unbound": changes.new_operators_unbound,
            },
        })

    for r in records:
        db.table("kuaimai_field_audit").insert({
            "org_id": org_id,
            "source": source,
            "audit_type": r["audit_type"],
            "changes": r["changes"],
            "sync_batch_id": sync_batch_id,
            "status": "new",
        }).execute()


# ──────────────────────── 主入口 ────────────────────────


async def sync_shop_operators(
    db: Any,
    *,
    org_id: str,
    company_label: str,
    kuaimai_company_id: int,
    response_rows: list[dict],
    sync_batch_id: str,
) -> SyncChanges:
    """
    主入口：处理 viperp 响应里的所有店铺/运营信息。

    步骤：
      1. 提取每行的店铺/运营
      2. UPSERT erp_shop_operators
      3. 标记消失的店铺
      4. 同步 erp_operators（自动匹配企微）
      5. 验证现有绑定
      6. 写 audit + 推告警
    """
    changes = SyncChanges()

    # 提取
    shops: list[ShopFromResponse] = []
    seen_shop_ids: set[int] = set()
    operator_names: set[str] = set()
    for row in response_rows:
        s = extract_shop_from_row(row)
        if s is None:
            continue
        shops.append(s)
        seen_shop_ids.add(s.shop_user_id)
        if s.operator_name:
            operator_names.add(s.operator_name)

    logger.info(
        f"shop_operator_sync 提取完成 | "
        f"shops={len(shops)} operators={len(operator_names)}"
    )

    # 同步店铺
    for shop in shops:
        try:
            _sync_shop_row(
                db,
                org_id=org_id,
                kuaimai_company_id=kuaimai_company_id,
                shop=shop,
                sync_batch_id=sync_batch_id,
                changes=changes,
            )
        except Exception as e:
            logger.error(
                f"shop_operator_sync upsert 失败 | "
                f"shop={shop.shop_user_id} | err={e}"
            )

    # 标记消失店铺
    _mark_removed_shops(
        db,
        org_id=org_id,
        kuaimai_company_id=kuaimai_company_id,
        seen_shop_ids=seen_shop_ids,
        changes=changes,
    )

    # 同步运营（含自动匹配企微）
    _sync_operators(db, org_id=org_id, operator_names=operator_names, changes=changes)

    # 验证现有绑定（自愈机制）
    _verify_existing_bindings(db, org_id=org_id, changes=changes)

    # 写 audit
    if changes.has_any:
        _write_audit_records(
            db,
            org_id=org_id,
            source="viperp",
            changes=changes,
            sync_batch_id=sync_batch_id,
        )

        # 推告警（异步）
        from services.kuaimai_external import wecom_alert
        msg = format_changes_alert(company_label=company_label, changes=changes)
        await wecom_alert.send_alert(org_id, msg)

    return changes


def format_changes_alert(*, company_label: str, changes: SyncChanges) -> str:
    """构造店铺/运营变化的企微告警 markdown。"""
    lines = [f"📊 **快麦店铺/运营变化** [{company_label}]", ""]

    if changes.new_shops:
        lines.append(f"🆕 **新店铺（{len(changes.new_shops)} 个）：**")
        for s in changes.new_shops[:5]:
            lines.append(
                f"  • {s['shop_name']} ({s.get('platform') or '-'}) "
                f"运营: {s.get('operator_name') or '未指定'}"
            )
        if len(changes.new_shops) > 5:
            lines.append(f"  ...(还有 {len(changes.new_shops) - 5} 个)")
        lines.append("")

    if changes.operator_changes:
        lines.append(f"🔄 **店铺换运营（{len(changes.operator_changes)} 个）：**")
        for c in changes.operator_changes[:10]:
            lines.append(
                f"  • {c['shop_name']}: "
                f"{c.get('old_operator') or '无'} → {c.get('new_operator') or '无'}"
            )
        lines.append("")

    if changes.removed_shops:
        lines.append(f"❌ **店铺消失（{len(changes.removed_shops)} 个）：**")
        for s in changes.removed_shops[:5]:
            lines.append(f"  • {s['shop_name']} (原运营: {s.get('last_operator') or '-'})")
        lines.append("")

    if changes.new_operators_auto_bound:
        lines.append(f"✅ **新运营自动绑定（{len(changes.new_operators_auto_bound)} 个）：**")
        for o in changes.new_operators_auto_bound[:10]:
            lines.append(f"  • {o['operator_name']} → 企微 {o['wecom_userid']}")
        lines.append("")

    if changes.new_operators_unbound:
        lines.append(f"⚠️ **新运营未绑定（{len(changes.new_operators_unbound)} 个，请手动处理）：**")
        for o in changes.new_operators_unbound[:10]:
            reason_map = {
                "not_found": "未找到同名企微员工",
                "multiple": f"找到 {o['matched_count']} 个同名（请选择）",
            }
            lines.append(
                f"  • {o['operator_name']} ({reason_map.get(o['reason'], o['reason'])})"
            )
        lines.append("")

    if changes.binding_invalidated:
        lines.append(f"🔓 **绑定失效自动解绑（{len(changes.binding_invalidated)} 个）：**")
        for b in changes.binding_invalidated[:10]:
            lines.append(
                f"  • {b['operator_name']} (旧 wecom_userid: {b['stale_wecom_userid']}，疑似离职)"
            )

    return "\n".join(lines)
