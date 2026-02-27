"""
清理异常的媒体任务消息

根据诊断结果删除异常的图片/视频任务消息。
"""

import os
import sys
import json
import argparse
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv(project_root / "backend" / ".env")

from supabase import create_client
from loguru import logger


def clean_media_messages(dry_run=True):
    """清理异常媒体任务消息"""

    # 读取待删除的 ID 列表
    ids_file = "/tmp/media_messages_to_delete.json"
    if not os.path.exists(ids_file):
        logger.error(f"未找到待删除列表文件：{ids_file}")
        logger.error("请先运行: python scripts/diagnose_media_messages.py")
        return

    with open(ids_file, "r") as f:
        ids_to_delete = json.load(f)

    if not ids_to_delete:
        logger.info("没有需要删除的消息")
        return

    logger.info(f"准备删除 {len(ids_to_delete)} 条消息")

    if dry_run:
        logger.warning("【模拟模式】以下消息将被删除（实际不会删除）：")
        for i, msg_id in enumerate(ids_to_delete[:10], 1):
            logger.info(f"  {i}. {msg_id}")
        if len(ids_to_delete) > 10:
            logger.info(f"  ... 还有 {len(ids_to_delete) - 10} 条")

        logger.info("\n如需真正执行删除，请运行：")
        logger.info("  python scripts/clean_media_messages.py --delete --confirm")
        return

    # 初始化 Supabase 客户端
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return

    db = create_client(supabase_url, supabase_key)

    # 批量删除（每次最多50条）
    batch_size = 50
    deleted_count = 0
    failed_count = 0

    for i in range(0, len(ids_to_delete), batch_size):
        batch = ids_to_delete[i:i + batch_size]
        logger.info(f"删除批次 {i // batch_size + 1}：{len(batch)} 条消息")

        try:
            result = db.table("messages").delete().in_("id", batch).execute()
            deleted_count += len(batch)
            logger.success(f"  ✅ 成功删除 {len(batch)} 条")
        except Exception as e:
            logger.error(f"  ❌ 删除失败：{e}")
            failed_count += len(batch)

    logger.info("\n" + "=" * 80)
    logger.info("清理完成")
    logger.info("=" * 80)
    logger.info(f"成功删除: {deleted_count} 条")
    logger.info(f"失败: {failed_count} 条")

    # 删除临时文件
    if deleted_count > 0:
        os.remove(ids_file)
        logger.info(f"\n已删除临时文件：{ids_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理异常的媒体任务消息")
    parser.add_argument("--delete", action="store_true", help="执行删除（默认为模拟模式）")
    parser.add_argument("--confirm", action="store_true", help="确认删除操作")

    args = parser.parse_args()

    if args.delete and not args.confirm:
        logger.error("需要同时指定 --delete 和 --confirm 才能执行删除")
        logger.error("示例：python scripts/clean_media_messages.py --delete --confirm")
        sys.exit(1)

    dry_run = not (args.delete and args.confirm)
    clean_media_messages(dry_run=dry_run)
