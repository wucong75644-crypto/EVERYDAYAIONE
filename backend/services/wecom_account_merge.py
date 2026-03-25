"""
企微账号合并服务

将企微用户（W）的数据迁移到 Web 用户（K），然后删除 W。
按 FK 依赖顺序操作，防止约束冲突。
"""

from loguru import logger



# 可直接 UPDATE user_id 的表（无唯一约束冲突风险）
MIGRATE_TABLES = [
    "conversations",
    "image_generations",
    "credits_history",
    "tasks",
    "credit_transactions",
]

# 有唯一约束的表：直接删除旧用户记录（K 保留自己的数据）
DELETE_TABLES = [
    "user_subscriptions",
    "user_memory_settings",
]


async def merge_users(
    db,
    keep_user_id: str,
    remove_user_id: str,
    wecom_userid: str,
    corp_id: str,
    nickname: str,
) -> None:
    """
    账号合并：保留 keep_user_id，迁移 remove_user_id 的数据后删除。

    Args:
        db: Supabase 客户端
        keep_user_id: 保留的用户 ID（Web 端用户，有手机号/密码）
        remove_user_id: 待删除的用户 ID（企微自动创建的用户）
        wecom_userid: 企微用户 ID
        corp_id: 企业 ID
        nickname: 企微昵称
    """
    logger.info(
        f"Merging users | keep={keep_user_id} | remove={remove_user_id} | "
        f"wecom_userid={wecom_userid}"
    )

    # Step 1: 迁移关联数据
    for table in MIGRATE_TABLES:
        db.table(table).update(
            {"user_id": keep_user_id}
        ).eq("user_id", remove_user_id).execute()

    for table in DELETE_TABLES:
        db.table(table).delete().eq("user_id", remove_user_id).execute()

    # admin_action_logs.target_user_id（非 FK，普通 UUID 字段）
    db.table("admin_action_logs").update(
        {"target_user_id": keep_user_id}
    ).eq("target_user_id", remove_user_id).execute()

    # Step 2: 合并积分
    _merge_credits(db, keep_user_id, remove_user_id)

    # Step 3: 更新映射（防唯一约束冲突）
    db.table("wecom_user_mappings").delete().eq(
        "user_id", remove_user_id
    ).execute()

    # 为 keep_user 创建 OAuth 映射（如不存在）
    existing_mapping = (
        db.table("wecom_user_mappings")
        .select("id")
        .eq("wecom_userid", wecom_userid)
        .eq("corp_id", corp_id)
        .limit(1)
        .execute()
    )
    if not existing_mapping.data:
        db.table("wecom_user_mappings").insert({
            "wecom_userid": wecom_userid,
            "corp_id": corp_id,
            "user_id": keep_user_id,
            "channel": "oauth",
            "wecom_nickname": nickname,
        }).execute()

    # Step 4: 更新 login_methods
    _add_login_method(db, keep_user_id, "wecom")

    # Step 5: 删除旧用户
    db.table("users").delete().eq("id", remove_user_id).execute()

    logger.info(
        f"Users merged | keep={keep_user_id} | removed={remove_user_id} | "
        f"wecom_userid={wecom_userid}"
    )


def _merge_credits(db, keep_user_id: str, remove_user_id: str) -> None:
    """合并积分：W 的积分转移到 K"""
    remove_user = (
        db.table("users").select("credits")
        .eq("id", remove_user_id).single().execute()
    )
    if not remove_user.data or remove_user.data["credits"] <= 0:
        return

    transfer_credits = remove_user.data["credits"]
    keep_user = (
        db.table("users").select("credits")
        .eq("id", keep_user_id).single().execute()
    )
    new_balance = (keep_user.data["credits"] if keep_user.data else 0) + transfer_credits

    db.table("users").update(
        {"credits": new_balance}
    ).eq("id", keep_user_id).execute()

    db.table("credits_history").insert({
        "user_id": keep_user_id,
        "change_amount": transfer_credits,
        "balance_after": new_balance,
        "change_type": "merge",
        "description": f"账号合并积分迁移（来自用户 {remove_user_id[:8]}）",
    }).execute()


def _add_login_method(db, user_id: str, method: str) -> None:
    """向用户的 login_methods 数组添加方法（去重）"""
    user = (
        db.table("users").select("login_methods")
        .eq("id", user_id).single().execute()
    )
    if user.data:
        methods = user.data.get("login_methods") or []
        if method not in methods:
            methods.append(method)
            db.table("users").update(
                {"login_methods": methods}
            ).eq("id", user_id).execute()
