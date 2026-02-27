"""
诊断并清理异常的媒体任务消息

检查数据库中的图片/视频任务消息，找出异常数据并提供清理方案。
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv(project_root / "backend" / ".env")

from supabase import create_client
from loguru import logger


def diagnose_media_messages():
    """诊断媒体任务消息"""

    # 初始化 Supabase 客户端
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return

    db = create_client(supabase_url, supabase_key)

    logger.info("=" * 80)
    logger.info("开始诊断媒体任务消息")
    logger.info("=" * 80)

    # 1. 查询所有图片/视频任务消息
    result = db.table("messages").select(
        "id, conversation_id, role, status, generation_params, content, created_at"
    ).in_(
        "generation_params->>type", ["image", "video"]
    ).order("created_at", desc=True).execute()

    messages = result.data
    logger.info(f"\n找到 {len(messages)} 条媒体任务消息")

    # 2. 分类统计
    stats = {
        "total": len(messages),
        "with_url": 0,  # 有 URL 的消息
        "without_url": 0,  # 没有 URL 的消息
        "pending": 0,  # pending 状态
        "completed": 0,  # completed 状态
        "failed": 0,  # failed 状态
        "by_type": {"image": 0, "video": 0},
    }

    # 异常消息列表
    anomalies = {
        "completed_without_url": [],  # completed 但没有 URL
        "old_pending": [],  # 超过24小时的 pending
        "empty_content": [],  # content 为空
    }

    for msg in messages:
        gen_type = msg["generation_params"].get("type")
        status = msg["status"]
        content = msg.get("content", [])
        created_at = datetime.fromisoformat(msg["created_at"].replace("Z", "+00:00"))

        # 统计类型
        if gen_type in stats["by_type"]:
            stats["by_type"][gen_type] += 1

        # 统计状态
        if status == "pending":
            stats["pending"] += 1
        elif status == "completed":
            stats["completed"] += 1
        elif status == "failed":
            stats["failed"] += 1

        # 检查是否有 URL（处理 content 可能是字符串的情况）
        has_url = False
        if isinstance(content, list):
            has_url = any(
                isinstance(c, dict) and c.get("type") in ["image", "video"] and c.get("url")
                for c in content
            )

        if has_url:
            stats["with_url"] += 1
        else:
            stats["without_url"] += 1

        # 检查异常情况

        # 异常1：completed 但没有 URL
        if status == "completed" and not has_url:
            anomalies["completed_without_url"].append({
                "id": msg["id"],
                "conversation_id": msg["conversation_id"],
                "type": gen_type,
                "content": content,
                "created_at": msg["created_at"],
            })

        # 异常2：超过24小时的 pending
        age_hours = (datetime.now(created_at.tzinfo) - created_at).total_seconds() / 3600
        if status == "pending" and age_hours > 24:
            anomalies["old_pending"].append({
                "id": msg["id"],
                "conversation_id": msg["conversation_id"],
                "type": gen_type,
                "age_hours": round(age_hours, 1),
                "created_at": msg["created_at"],
            })

        # 异常3：content 为空
        if not content or len(content) == 0:
            anomalies["empty_content"].append({
                "id": msg["id"],
                "conversation_id": msg["conversation_id"],
                "type": gen_type,
                "status": status,
                "created_at": msg["created_at"],
            })

    # 3. 输出统计报告
    logger.info("\n" + "=" * 80)
    logger.info("统计报告")
    logger.info("=" * 80)
    logger.info(f"总计: {stats['total']} 条")
    logger.info(f"  - 图片任务: {stats['by_type']['image']} 条")
    logger.info(f"  - 视频任务: {stats['by_type']['video']} 条")
    logger.info(f"\n状态分布:")
    logger.info(f"  - pending: {stats['pending']} 条")
    logger.info(f"  - completed: {stats['completed']} 条")
    logger.info(f"  - failed: {stats['failed']} 条")
    logger.info(f"\nURL 情况:")
    logger.info(f"  - 有 URL: {stats['with_url']} 条")
    logger.info(f"  - 无 URL: {stats['without_url']} 条")

    # 4. 输出异常报告
    logger.info("\n" + "=" * 80)
    logger.info("异常报告")
    logger.info("=" * 80)

    logger.info(f"\n【异常1】completed 但没有 URL: {len(anomalies['completed_without_url'])} 条")
    if anomalies["completed_without_url"]:
        logger.warning("这些消息标记为 completed，但 content 中没有图片/视频 URL")
        logger.warning("建议操作：删除这些消息")
        for i, item in enumerate(anomalies["completed_without_url"][:5], 1):
            logger.info(f"  {i}. {item['id']} | {item['type']} | {item['created_at']}")
        if len(anomalies["completed_without_url"]) > 5:
            logger.info(f"  ... 还有 {len(anomalies['completed_without_url']) - 5} 条")

    logger.info(f"\n【异常2】超过24小时的 pending: {len(anomalies['old_pending'])} 条")
    if anomalies["old_pending"]:
        logger.warning("这些消息已经 pending 超过24小时，很可能是任务失败但状态未更新")
        logger.warning("建议操作：删除这些消息")
        for i, item in enumerate(anomalies["old_pending"][:5], 1):
            logger.info(f"  {i}. {item['id']} | {item['type']} | {item['age_hours']}小时 | {item['created_at']}")
        if len(anomalies["old_pending"]) > 5:
            logger.info(f"  ... 还有 {len(anomalies['old_pending']) - 5} 条")

    logger.info(f"\n【异常3】content 为空: {len(anomalies['empty_content'])} 条")
    if anomalies["empty_content"]:
        logger.warning("这些消息的 content 字段为空，无法显示任何内容")
        logger.warning("建议操作：删除这些消息")
        for i, item in enumerate(anomalies["empty_content"][:5], 1):
            logger.info(f"  {i}. {item['id']} | {item['type']} | {item['status']} | {item['created_at']}")
        if len(anomalies["empty_content"]) > 5:
            logger.info(f"  ... 还有 {len(anomalies['empty_content']) - 5} 条")

    # 5. 生成清理脚本
    logger.info("\n" + "=" * 80)
    logger.info("清理建议")
    logger.info("=" * 80)

    # 收集所有需要删除的消息 ID
    ids_to_delete = set()
    ids_to_delete.update(item["id"] for item in anomalies["completed_without_url"])
    ids_to_delete.update(item["id"] for item in anomalies["old_pending"])
    ids_to_delete.update(item["id"] for item in anomalies["empty_content"])

    if ids_to_delete:
        logger.info(f"\n建议删除 {len(ids_to_delete)} 条异常消息")
        logger.info("请确认后执行以下命令：")
        logger.info(f"\n  python scripts/clean_media_messages.py --delete")

        # 保存待删除的 ID 列表
        import json
        with open("/tmp/media_messages_to_delete.json", "w") as f:
            json.dump(list(ids_to_delete), f, indent=2)
        logger.info(f"\n待删除的消息 ID 已保存到：/tmp/media_messages_to_delete.json")
    else:
        logger.info("\n✅ 未发现需要清理的异常消息")

    logger.info("\n" + "=" * 80)
    logger.info("诊断完成")
    logger.info("=" * 80)


if __name__ == "__main__":
    diagnose_media_messages()
