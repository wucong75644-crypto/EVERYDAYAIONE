"""
合并企微重复账号脚本（一次性运行）

逻辑：
1. 扫描所有 (nickname, created_by='wecom') 重复组（count > 1）
2. 对每组排序：has_conversations DESC, has_mapping DESC, created_at ASC
   → 选第 1 个作为 keep_uid（优先有数据 / 有 mapping / 最早创建的）
3. dry-run（默认）：打印每组合并预估
4. --apply：调 RPC merge_wecom_duplicate_users 实际合并

用法：
  python scripts/merge_wecom_duplicate_users.py                              # 干跑
  python scripts/merge_wecom_duplicate_users.py --apply                      # 真跑（全部汇总积分）
  python scripts/merge_wecom_duplicate_users.py --apply --zero-drops-credits # 真跑（drops 积分清零）
  python scripts/merge_wecom_duplicate_users.py --apply --yes                # 真跑且跳过逐组确认

--zero-drops-credits：合并前把 drop_uids 的 credits 改成 0，
                      KEEP 保持原始积分（适用于"bug 期间多发的积分不该汇总"场景）
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras


def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("❌ 缺少 DATABASE_URL", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(url, connect_timeout=15)


def find_duplicate_groups(cur) -> list[dict]:
    """找所有重复 wecom 用户组。返回 [{nickname, users: [{...}]}]"""
    cur.execute("""
    SELECT u.nickname, u.id::text AS id, u.created_at,
           u.credits,
           EXISTS (SELECT 1 FROM wecom_user_mappings wm WHERE wm.user_id = u.id) AS has_mapping,
           (SELECT count(*) FROM conversations c WHERE c.user_id = u.id) AS conv_count,
           (SELECT count(*) FROM tasks t WHERE t.user_id = u.id) AS task_count,
           (SELECT count(*) FROM image_generations ig WHERE ig.user_id = u.id) AS img_count
    FROM users u
    WHERE u.created_by = 'wecom'
      AND u.nickname IN (
        SELECT nickname FROM users
        WHERE created_by = 'wecom'
        GROUP BY nickname
        HAVING count(*) > 1
      )
    ORDER BY u.nickname, u.created_at;
    """)
    rows = cur.fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["nickname"], []).append(dict(r))

    # 每组按 has_data DESC, has_mapping DESC, created_at ASC 排序
    result = []
    for nick, users in groups.items():
        users.sort(
            key=lambda u: (
                -(u["conv_count"] + u["task_count"] + u["img_count"]),  # 有数据优先
                -int(u["has_mapping"]),                                  # 有映射优先
                u["created_at"],                                          # 最早创建
            )
        )
        result.append({"nickname": nick, "users": users})

    return result


def print_group(group: dict, idx: int) -> None:
    nick = group["nickname"]
    users = group["users"]
    keep = users[0]
    drops = users[1:]

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"【组 {idx}】「{nick}」共 {len(users)} 个账号，合并后保留 1 个，删除 {len(drops)} 个")
    print(f"")
    print(f"  ✅ KEEP  {keep['id'][:8]} | created={keep['created_at']} | "
          f"积分={keep['credits']} | 对话={keep['conv_count']} | "
          f"任务={keep['task_count']} | 图={keep['img_count']} | 映射={keep['has_mapping']}")
    for d in drops:
        print(f"  ❌ DROP  {d['id'][:8]} | created={d['created_at']} | "
              f"积分={d['credits']} | 对话={d['conv_count']} | "
              f"任务={d['task_count']} | 图={d['img_count']} | 映射={d['has_mapping']}")

    # 汇总
    moved_credits = sum(d["credits"] for d in drops)
    moved_data = sum(d["conv_count"] + d["task_count"] + d["img_count"] for d in drops)
    print(f"")
    print(f"  📊 合并后 KEEP 将获得：积分 +{moved_credits}（{keep['credits']} → "
          f"{keep['credits'] + moved_credits}），迁移数据 {moved_data} 条")


def apply_group(cur, group: dict, zero_drops_credits: bool = False) -> dict:
    """对一组执行合并 RPC

    Args:
        zero_drops_credits: True → 先把 drop_uids 的 credits 改成 0
                            （使 merge RPC 累加 0 → KEEP 保持原积分）
    """
    keep_uid = group["users"][0]["id"]
    drop_uids = [u["id"] for u in group["users"][1:]]

    if zero_drops_credits:
        # 同一事务内：先清零 drop 积分，再调 merge RPC
        # 任一失败整组回滚（main 的 conn.autocommit=False）
        cur.execute(
            "UPDATE users SET credits = 0 WHERE id = ANY(%s::uuid[]);",
            (drop_uids,),
        )

    cur.execute(
        "SELECT merge_wecom_duplicate_users(%s::uuid, %s::uuid[]) AS result;",
        (keep_uid, drop_uids),
    )
    row = cur.fetchone()
    # 兼容 RealDictCursor (dict) 和 普通 cursor (tuple)
    return row["result"] if isinstance(row, dict) else row[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真跑（默认 dry-run）")
    parser.add_argument("--yes", action="store_true", help="跳过逐组确认")
    parser.add_argument(
        "--zero-drops-credits", action="store_true",
        help="合并前把 drop_uids 的 credits 清 0，KEEP 保持原积分（方案 B）",
    )
    args = parser.parse_args()

    conn = get_db()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        groups = find_duplicate_groups(cur)
        if not groups:
            print("✅ 无重复用户组")
            return

        print(f"\n找到 {len(groups)} 个重复用户组：")
        total_users = sum(len(g["users"]) for g in groups)
        total_drops = sum(len(g["users"]) - 1 for g in groups)
        total_credits = sum(sum(u["credits"] for u in g["users"][1:]) for g in groups)
        print(f"  总账号数: {total_users}")
        print(f"  待删除: {total_drops}")
        if args.zero_drops_credits:
            print(f"  drop 积分: 清 0（方案 B，KEEP 保持原积分） — 实际累加 0")
        else:
            print(f"  待迁移积分: {total_credits}（方案 A 全部汇总到 KEEP）")

        for i, g in enumerate(groups, 1):
            print_group(g, i)

        if not args.apply:
            print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"\n🔍 DRY-RUN 模式（未执行）。确认无误后加 --apply 真跑。")
            return

        # --apply 模式
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"\n⚠️  即将执行合并（不可逆）")
        if not args.yes:
            ans = input("输入 'MERGE' 确认继续: ")
            if ans.strip() != "MERGE":
                print("❌ 已取消")
                return

        results = []
        for i, g in enumerate(groups, 1):
            print(f"\n执行组 {i}/{len(groups)}「{g['nickname']}」...")
            result = apply_group(cur, g, zero_drops_credits=args.zero_drops_credits)
            conn.commit()
            print(f"  ✅ {result}")
            results.append({"nickname": g["nickname"], **result})

        # 总报告
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"\n✅ 合并完成 ({len(results)} 组)")
        total = sum(r["dropped"] for r in results)
        total_cred = sum(r["merged_credits"] for r in results)
        print(f"  共删除 {total} 个账号，迁移积分 {total_cred}")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ 执行失败已回滚: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
