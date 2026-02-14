"""
数据迁移：将旧格式的消息 content 转换为新格式

旧格式：
  content = "文本内容"（字符串）
  image_url = "xxx"（独立字段）
  video_url = "yyy"（独立字段）

新格式：
  content = [
    {"type": "text", "text": "文本内容"},
    {"type": "image", "url": "xxx", "width": 4096, "height": 4096},
    {"type": "video", "url": "yyy"}
  ]（JSON 数组）
"""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client
from loguru import logger
import json

# 加载环境变量
load_dotenv()


def migrate_message_content_format():
    """迁移消息 content 格式"""

    # 连接 Supabase
    supabase = create_client(
        os.getenv('SUPABASE_URL'),
        os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    )

    # 1. 查询所有 assistant 消息
    logger.info("开始查询所有 assistant 消息...")
    response = supabase.table('messages').select('*').eq('role', 'assistant').execute()

    total = len(response.data)
    logger.info(f"共找到 {total} 条 assistant 消息")

    # 2. 检查并转换格式
    migrated_count = 0
    skipped_count = 0
    error_count = 0

    for msg in response.data:
        msg_id = msg['id']
        content = msg['content']
        image_url = msg.get('image_url')
        video_url = msg.get('video_url')

        # 判断是否需要迁移
        needs_migration = False
        new_content = []

        # 检查 content 格式
        if isinstance(content, str):
            # 尝试解析为 JSON
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    # 已经是新格式（JSON 数组）
                    skipped_count += 1
                    continue
                else:
                    # JSON 但不是数组，需要转换
                    needs_migration = True
                    new_content.append({"type": "text", "text": str(parsed)})
            except json.JSONDecodeError:
                # 纯字符串，需要转换
                needs_migration = True
                # 只有当 content 不为空且不是占位符文本时才添加
                if content and content not in ["", "生成中...", "处理中..."]:
                    new_content.append({"type": "text", "text": content})
        elif isinstance(content, list):
            # 已经是新格式
            skipped_count += 1
            continue
        else:
            # 其他类型（如 None），需要转换
            needs_migration = True
            new_content = []

        # 如果有 image_url 或 video_url，添加到 new_content
        if image_url:
            needs_migration = True
            new_content.append({
                "type": "image",
                "url": image_url,
                "width": 4096,  # 默认尺寸
                "height": 4096,
            })

        if video_url:
            needs_migration = True
            new_content.append({
                "type": "video",
                "url": video_url,
            })

        # 执行迁移
        if needs_migration:
            try:
                # 更新消息
                update_data = {
                    "content": new_content,
                }

                supabase.table('messages').update(update_data).eq('id', msg_id).execute()

                migrated_count += 1
                logger.info(
                    f"✅ 迁移成功 | msg_id={msg_id} | "
                    f"old_content={content[:50] if isinstance(content, str) else content} | "
                    f"new_content={new_content}"
                )
            except Exception as e:
                error_count += 1
                logger.error(f"❌ 迁移失败 | msg_id={msg_id} | error={e}")

    # 3. 输出统计
    logger.info(
        f"\n迁移完成！\n"
        f"总计: {total} 条\n"
        f"✅ 已迁移: {migrated_count} 条\n"
        f"⏭️  已跳过（无需迁移）: {skipped_count} 条\n"
        f"❌ 失败: {error_count} 条"
    )

    return {
        "total": total,
        "migrated": migrated_count,
        "skipped": skipped_count,
        "error": error_count,
    }


if __name__ == "__main__":
    logger.info("开始数据迁移...")
    result = migrate_message_content_format()

    if result["error"] > 0:
        logger.error("迁移过程中出现错误，请检查日志")
        sys.exit(1)
    else:
        logger.success("迁移成功完成！")
        sys.exit(0)
