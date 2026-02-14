#!/usr/bin/env python3
"""
清理孤儿任务脚本

功能：
1. 查找所有 conversation_id 为 NULL 的任务
2. 将 running/pending 状态的任务标记为 failed
3. 记录清理日志

使用方法：
cd backend && python3 scripts/cleanup_orphan_tasks.py
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, UTC
from core.database import get_supabase_client
from loguru import logger


def cleanup_orphan_tasks():
    """清理孤儿任务"""
    db = get_supabase_client()

    # 1. 查询所有孤儿任务
    logger.info("正在查询孤儿任务...")
    orphan_tasks = db.table('tasks').select(
        'id,external_task_id,status,type,created_at'
    ).is_('conversation_id', 'null').execute()

    if not orphan_tasks.data:
        logger.info("✅ 没有发现孤儿任务")
        return

    total = len(orphan_tasks.data)
    logger.warning(f"发现 {total} 个孤儿任务（conversation_id 为 NULL）")

    # 2. 统计不同状态的任务
    running_pending = [
        t for t in orphan_tasks.data
        if t['status'] in ['running', 'pending']
    ]
    completed_failed = [
        t for t in orphan_tasks.data
        if t['status'] in ['completed', 'failed']
    ]

    logger.info(f"  - running/pending: {len(running_pending)} 个")
    logger.info(f"  - completed/failed: {len(completed_failed)} 个")

    # 3. 处理 running/pending 任务
    if running_pending:
        logger.info(f"\n正在标记 {len(running_pending)} 个进行中的任务为 failed...")

        task_ids = [t['id'] for t in running_pending]
        result = db.table('tasks').update({
            'status': 'failed',
            'error_message': 'Orphan task: no conversation_id (cleaned by script)',
            'completed_at': datetime.now(UTC).isoformat()
        }).in_('id', task_ids).execute()

        logger.success(f"✅ 已更新 {len(result.data)} 个任务状态")

    # 4. 输出摘要
    logger.info("\n" + "="*60)
    logger.info("清理完成！")
    logger.info(f"总计处理: {total} 个孤儿任务")
    logger.info(f"  - 标记为 failed: {len(running_pending)} 个")
    logger.info(f"  - 保持原状态: {len(completed_failed)} 个")
    logger.info("="*60)

    # 5. 输出详细列表
    if running_pending:
        logger.info("\n已清理的任务 ID:")
        for task in running_pending:
            logger.info(f"  - {task['external_task_id']} ({task['type']}) - {task['created_at']}")


if __name__ == "__main__":
    try:
        cleanup_orphan_tasks()
    except Exception as e:
        logger.error(f"清理失败: {e}")
        sys.exit(1)
