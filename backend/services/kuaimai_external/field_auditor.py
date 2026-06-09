"""
字段差异检测 — 对比本次 sync 拿到的字段集合 vs 上次

工作流程：
  1. 每次 sync 末尾调 audit_response()
  2. 提取这次响应的所有顶层字段名
  3. 跟 DB 里"上次的快照"对比
  4. 检测：新增字段 / 消失字段 / 类型变化
  5. 有差异 → 写 kuaimai_field_audit 表 + 推企微告警

注意：业务字段值变化不告警（那是日常），只关心 schema 变化。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ──────────────────────── 工具 ────────────────────────


def infer_value_type(val: Any) -> str:
    """推断字段语义类型（跟 dump_thinktank_fields.py 保持一致）。"""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "numeric"
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return "string(empty)"
        try:
            time.strptime(s, "%Y-%m-%d")
            return "date"
        except ValueError:
            pass
        try:
            float(s)
            return "numeric(str)" if "." in s else "integer(str)"
        except ValueError:
            return "string"
    if isinstance(val, list):
        return "array"
    if isinstance(val, dict):
        return "object"
    return type(val).__name__


def extract_field_types(sample_row: dict) -> dict[str, str]:
    """提取一行的"字段名 → 类型"映射。"""
    return {k: infer_value_type(v) for k, v in sample_row.items()}


def extract_field_samples(sample_row: dict, field_names: list[str]) -> dict[str, str]:
    """提取指定字段的样例值（截断到 50 字符）。"""
    out: dict[str, str] = {}
    for name in field_names:
        if name in sample_row:
            val = sample_row[name]
            if isinstance(val, (dict, list)):
                out[name] = f"<{type(val).__name__}>"
            else:
                out[name] = str(val)[:50]
    return out


# ──────────────────────── 差异检测 ────────────────────────


@dataclass
class FieldDiff:
    """字段差异结果"""
    new_fields: list[dict] = field(default_factory=list)
    # [{"name": "xxx", "inferred_type": "numeric", "sample": "0.00"}]

    disappeared_fields: list[str] = field(default_factory=list)
    # ["old_field_1", "old_field_2"]

    type_changed_fields: list[dict] = field(default_factory=list)
    # [{"name": "xxx", "old_type": "integer", "new_type": "string", "sample": "abc"}]

    @property
    def has_changes(self) -> bool:
        return bool(
            self.new_fields or self.disappeared_fields or self.type_changed_fields
        )


def diff_fields(
    *,
    current: dict[str, str],         # {字段名: 类型}
    previous: dict[str, str] | None,  # 上次的快照（首次 sync 为 None）
    sample_row: dict,                # 用来取样例值
) -> FieldDiff:
    """
    对比当前字段集合和上次快照。

    首次 sync（previous=None）→ 不视为差异，全部字段当作"已知基线"。
    """
    diff = FieldDiff()
    if previous is None:
        return diff  # 首次同步，不产生差异告警

    current_names = set(current.keys())
    previous_names = set(previous.keys())

    # 新增字段
    new_names = sorted(current_names - previous_names)
    new_samples = extract_field_samples(sample_row, new_names)
    for name in new_names:
        diff.new_fields.append({
            "name": name,
            "inferred_type": current[name],
            "sample": new_samples.get(name, ""),
        })

    # 消失字段
    diff.disappeared_fields = sorted(previous_names - current_names)

    # 类型变化
    common = current_names & previous_names
    for name in sorted(common):
        old_t = previous[name]
        new_t = current[name]
        # 容忍 string/integer(str) 这种边缘抖动（同一字段不同行可能值类型不一样）
        if old_t != new_t and not _is_compatible_type_drift(old_t, new_t):
            diff.type_changed_fields.append({
                "name": name,
                "old_type": old_t,
                "new_type": new_t,
                "sample": str(sample_row.get(name, ""))[:50],
            })

    return diff


def _is_compatible_type_drift(old_t: str, new_t: str) -> bool:
    """某些类型差异不告警（因为推断本身有歧义）。"""
    compat = {
        ("string(empty)", "string"),
        ("string", "string(empty)"),
        ("integer(str)", "numeric(str)"),
        ("numeric(str)", "integer(str)"),
        ("integer", "numeric"),
        ("numeric", "integer"),
        ("null", "string(empty)"),
        ("string(empty)", "null"),
    }
    return (old_t, new_t) in compat


# ──────────────────────── DB 写入 ────────────────────────


async def _get_last_snapshot(
    db: Any,
    *,
    org_id: str,
    source: str,
) -> dict[str, str] | None:
    """从 kuaimai_field_audit 取该 source 最近一次的字段快照。"""
    resp = await (
        db.table("kuaimai_field_audit")
        .select("all_fields_snapshot")
        .eq("org_id", org_id)
        .eq("source", source)
        .eq("audit_type", "field_change")
        .order("detected_at", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    snap = resp.data[0].get("all_fields_snapshot")
    if not snap or not isinstance(snap, dict):
        return None
    return snap


async def _save_audit_record(
    db: Any,
    *,
    org_id: str,
    source: str,
    diff: FieldDiff,
    current_snapshot: dict[str, str],
    sync_batch_id: str | None,
) -> str | None:
    """把 diff 写入 kuaimai_field_audit 表。"""
    record = {
        "org_id": org_id,
        "source": source,
        "audit_type": "field_change",
        "new_fields": diff.new_fields,
        "disappeared_fields": diff.disappeared_fields,
        "type_changed_fields": diff.type_changed_fields,
        "all_fields_snapshot": current_snapshot,
        "sync_batch_id": sync_batch_id,
        "status": "new",
    }
    resp = await db.table("kuaimai_field_audit").insert(record).execute()
    if not resp.data:
        return None
    return resp.data[0]["id"]


async def _save_baseline_snapshot(
    db: Any,
    *,
    org_id: str,
    source: str,
    current_snapshot: dict[str, str],
    sync_batch_id: str | None,
) -> None:
    """首次 sync 写一条"基线"记录（无差异，仅为下次对比留底）。"""
    await db.table("kuaimai_field_audit").insert({
        "org_id": org_id,
        "source": source,
        "audit_type": "field_change",
        "new_fields": [],
        "disappeared_fields": [],
        "type_changed_fields": [],
        "all_fields_snapshot": current_snapshot,
        "sync_batch_id": sync_batch_id,
        "status": "acknowledged",
        "notes": "首次同步基线快照",
    }).execute()


# ──────────────────────── 主入口 ────────────────────────


def format_alert_message(
    *,
    source: str,
    company_label: str,
    diff: FieldDiff,
) -> str:
    """构造企微告警 markdown 文案。"""
    lines = [
        f"⚠️ **快麦{source}字段变化提醒** [{company_label}]",
        "",
    ]
    if diff.new_fields:
        lines.append(f"🆕 **新增字段（{len(diff.new_fields)} 个）：**")
        for f in diff.new_fields[:10]:  # 最多展示 10 个
            lines.append(
                f"  • `{f['name']}` ({f['inferred_type']}, 样例: `{f['sample']}`)"
            )
        if len(diff.new_fields) > 10:
            lines.append(f"  ...(还有 {len(diff.new_fields) - 10} 个)")
        lines.append("")

    if diff.disappeared_fields:
        lines.append(f"❌ **消失字段（{len(diff.disappeared_fields)} 个）：**")
        for name in diff.disappeared_fields[:10]:
            lines.append(f"  • `{name}`")
        lines.append("")

    if diff.type_changed_fields:
        lines.append(f"🔄 **类型变化（{len(diff.type_changed_fields)} 个）：**")
        for f in diff.type_changed_fields[:10]:
            lines.append(
                f"  • `{f['name']}`: {f['old_type']} → {f['new_type']} (样例: `{f['sample']}`)"
            )
        lines.append("")

    lines.extend([
        "ℹ️ 数据已自动存入 `raw_payload`，**不会丢失**。",
        "如需建独立列方便 SQL 查询，请到后台字段管理页处理。",
    ])
    return "\n".join(lines)


async def audit_response(
    db: Any,
    *,
    org_id: str,
    source: str,                     # thinktank / viperp
    company_label: str,              # 企业可读名（蓝创/...）—— 仅用于告警
    sample_row: dict,                # 响应数据里一行（用来提取字段集合 + 类型）
    sync_batch_id: str | None = None,
) -> FieldDiff:
    """
    主入口：检测字段变化 + 写 audit + 推告警。

    Args:
        db: 同步 DB（用于读写 audit 表）

    Returns:
        FieldDiff（即使没差异也返回空 diff，方便调用方记日志）
    """
    if not sample_row:
        logger.debug(f"FieldAuditor sample_row 为空，跳过 | source={source}")
        return FieldDiff()

    current = extract_field_types(sample_row)
    previous = await _get_last_snapshot(db, org_id=org_id, source=source)

    if previous is None:
        # 首次同步，写基线，不告警
        await _save_baseline_snapshot(
            db,
            org_id=org_id,
            source=source,
            current_snapshot=current,
            sync_batch_id=sync_batch_id,
        )
        logger.info(
            f"FieldAuditor 首次基线快照已建立 | "
            f"org={org_id} source={source} fields={len(current)}"
        )
        return FieldDiff()

    diff = diff_fields(
        current=current,
        previous=previous,
        sample_row=sample_row,
    )

    if not diff.has_changes:
        logger.debug(
            f"FieldAuditor 字段无变化 | "
            f"org={org_id} source={source} fields={len(current)}"
        )
        return diff

    # 有差异：写 audit + 推告警
    audit_id = await _save_audit_record(
        db,
        org_id=org_id,
        source=source,
        diff=diff,
        current_snapshot=current,
        sync_batch_id=sync_batch_id,
    )
    logger.warning(
        f"FieldAuditor 检测到字段变化 | "
        f"org={org_id} source={source} "
        f"new={len(diff.new_fields)} disappeared={len(diff.disappeared_fields)} "
        f"changed={len(diff.type_changed_fields)} audit_id={audit_id}"
    )

    # 推送企微告警（异步、best-effort，不影响主流程）
    from services.kuaimai_external import wecom_alert
    msg = format_alert_message(
        source=source,
        company_label=company_label,
        diff=diff,
    )
    await wecom_alert.send_alert(org_id, msg)

    return diff
