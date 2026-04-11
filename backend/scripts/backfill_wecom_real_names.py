#!/usr/bin/env python3
"""
企微真名回填脚本（一次性运维）

背景：
2026-04 之前 wecom_message_service.py 调用 get_or_create_user 时未传 nickname,
导致所有企微用户都被命名为 "企微用户_xxxxxxxx"。本脚本对每个 wecom_user_mappings
中名字仍是兜底的用户，调企微 user/get 接口拉真名，回填到：
- users.nickname
- wecom_user_mappings.wecom_nickname

实现策略：
- 不依赖全量通讯录同步（user/simplelist 需要"通讯录管理"权限,普通自建应用没有）
- 使用 cgi-bin/user/get 单查接口，自建应用 token + 应用可见范围内即可调
- 凭证从 organizations.wecom_corp_id + org_configs.wecom_agent_secret 解析
- 按 (org_id, corp_id) 分组，每组只解一次凭证、只拿一次 token

使用方法：
    cd backend && source venv/bin/activate

    # 1. 先 dry-run（**会真实调用企微 user/get** 拉真名，但不写库）
    #    用于预览影响范围、验证可见范围设置是否正确
    python3 scripts/backfill_wecom_real_names.py --dry-run

    # 2. 确认无误后正式执行（再次调用 user/get + 写库）
    python3 scripts/backfill_wecom_real_names.py --execute

    # 限定单个企业（可选）
    python3 scripts/backfill_wecom_real_names.py --dry-run --org-id <uuid>

注意：
- dry-run 也会调企微 API（API 调用就是为了拿到要回填的名字）。
- execute 会再调一次。蓝创 16 人 ≈ 32 次调用，远低于配额。
- 幂等：可重复运行。已是真名的用户不会被改回去（只更新 "企微用户_" 前缀的兜底名）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.database import get_db
from services.wecom.wecom_contact_api import fetch_wecom_real_name


PLACEHOLDER_PREFIX = "企微用户_"


def list_placeholder_mappings(db, org_id: str | None) -> list[dict]:
    """
    查询所有 wecom_nickname 仍是兜底名的映射记录。

    Returns:
        [{"user_id", "wecom_userid", "corp_id", "org_id", "old_nickname"}, ...]
    """
    query = (
        db.table("wecom_user_mappings")
        .select("user_id, wecom_userid, corp_id, org_id, wecom_nickname")
        .like("wecom_nickname", f"{PLACEHOLDER_PREFIX}%")
    )
    if org_id:
        query = query.eq("org_id", org_id)
    rows = (query.execute().data or [])
    return [
        {
            "user_id": r["user_id"],
            "wecom_userid": r["wecom_userid"],
            "corp_id": r["corp_id"],
            "org_id": r.get("org_id"),
            "old_nickname": r["wecom_nickname"],
        }
        for r in rows
    ]


async def resolve_real_names(db, mappings: list[dict]) -> list[dict]:
    """
    对每条映射调企微 user/get 拿真名。

    跳过 org_id 为空的记录（散客企微用户，没有 per-org 凭证可用）。

    Returns:
        新增 "new_name" 字段的子集（只保留实际拿到真名的）
    """
    candidates: list[dict] = []
    for m in mappings:
        if not m.get("org_id"):
            continue
        name = await fetch_wecom_real_name(db, m["org_id"], m["wecom_userid"])
        if name and name != m["old_nickname"]:
            candidates.append({**m, "new_name": name})
    return candidates


def apply_backfill(db, candidates: list[dict]) -> dict:
    """
    正式执行回填。

    每个 candidate 做两件事：
    1. UPDATE users SET nickname = new_name WHERE id = user_id
       （只在当前 nickname 也是 "企微用户_" 兜底时才更新，避免覆盖手动改的真名）
    2. UPDATE wecom_user_mappings SET wecom_nickname = new_name WHERE user_id+corp_id
    """
    stats = {"users_updated": 0, "mappings_updated": 0, "users_skipped": 0, "errors": 0}

    for c in candidates:
        try:
            # 1. 更新 users.nickname（仅当 nickname 仍是兜底名时）
            user_resp = (
                db.table("users")
                .select("nickname")
                .eq("id", c["user_id"])
                .maybe_single()
                .execute()
            )
            if user_resp and user_resp.data:
                cur_nick = user_resp.data.get("nickname", "")
                if cur_nick.startswith(PLACEHOLDER_PREFIX):
                    db.table("users").update({
                        "nickname": c["new_name"],
                    }).eq("id", c["user_id"]).execute()
                    stats["users_updated"] += 1
                else:
                    stats["users_skipped"] += 1

            # 2. 更新 wecom_user_mappings.wecom_nickname
            db.table("wecom_user_mappings").update({
                "wecom_nickname": c["new_name"],
            }).eq("user_id", c["user_id"]).eq("corp_id", c["corp_id"]).execute()
            stats["mappings_updated"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.warning(
                f"Backfill failed | user_id={c['user_id']} | "
                f"wecom_userid={c['wecom_userid']} | error={e}"
            )

    return stats


async def amain(args):
    db = get_db()

    logger.info(
        f"开始回填 | mode={'execute' if args.execute else 'dry-run'} | "
        f"org_id={args.org_id or 'ALL'}"
    )

    # 1. 查所有兜底名映射
    try:
        mappings = list_placeholder_mappings(db, args.org_id)
    except Exception as e:
        logger.error(f"查询兜底名映射失败 | error={e}")
        sys.exit(1)

    if not mappings:
        logger.info("没有兜底名记录需要回填，全部映射都已是真名（或表为空）")
        return

    logger.info(f"找到 {len(mappings)} 条兜底名映射，开始调企微 user/get 拉真名...")

    # 2. 调企微 API 拉真名（按需，每个 userid 一次）
    try:
        candidates = await resolve_real_names(db, mappings)
    except Exception as e:
        logger.error(f"调用企微 user/get 失败 | error={e}")
        sys.exit(1)

    if not candidates:
        logger.warning(
            "企微 user/get 没拿到任何可用真名（可能这些 userid 不在自建应用可见范围，"
            "或凭证未配置）。检查：\n"
            "  1. organizations.wecom_corp_id 是否设置\n"
            "  2. org_configs.wecom_agent_secret 是否配置\n"
            "  3. 自建应用可见范围是否覆盖这些员工"
        )
        return

    # 3. 输出预览（前 10 条）
    logger.info(f"成功拿到 {len(candidates)} 个真名，前 10 条预览：")
    for c in candidates[:10]:
        logger.info(
            f"  {c['old_nickname']:30s} → {c['new_name']:20s} "
            f"(wecom_userid={c['wecom_userid']}, org_id={c['org_id']})"
        )
    if len(candidates) > 10:
        logger.info(f"  ... 还有 {len(candidates) - 10} 条未显示")

    # 4. dry-run 在此停止
    if args.dry_run:
        logger.info("dry-run 完成。如确认无误，加 --execute 正式执行。")
        return

    # 5. 正式执行
    logger.info("开始正式回填...")
    stats = apply_backfill(db, candidates)
    logger.info(
        f"回填完成 | users_updated={stats['users_updated']} | "
        f"users_skipped={stats['users_skipped']} | "
        f"mappings_updated={stats['mappings_updated']} | "
        f"errors={stats['errors']}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="企微真名回填（按需 user/get → users + wecom_user_mappings）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="拉真名但不写库（默认行为）",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="正式执行回填（与 --dry-run 互斥）",
    )
    parser.add_argument(
        "--org-id", type=str, default=None,
        help="可选：限定单个企业 org_id",
    )
    args = parser.parse_args()

    if args.execute and args.dry_run:
        logger.error("--execute 和 --dry-run 不能同时指定")
        sys.exit(2)
    if not args.execute and not args.dry_run:
        # 默认 dry-run，避免误操作
        args.dry_run = True
        logger.info("未指定 --execute / --dry-run，默认 dry-run 模式")

    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
