"""
测试孤儿任务修复效果

使用方法：
1. 从前端发起一次 Chat 对话（输入任意文本，如"你好"）
2. 运行此脚本：python3 test_orphan_fix.py
3. 查看最新任务的 version 和 started_at 是否正确
"""


import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).parent.parent / '.env')


def main():
    db = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))

    print("=" * 70)
    print("孤儿任务修复效果测试")
    print("=" * 70)

    # 查询最近的任务
    recent_tasks = db.table('tasks').select(
        'external_task_id, type, status, version, started_at, created_at, completed_at'
    ).order('created_at', desc=True).limit(10).execute()

    print("\n最近 10 个任务：\n")

    fixed_count = 0
    orphan_count = 0
    pending_count = 0

    for i, task in enumerate(recent_tasks.data, 1):
        task_type = task['type'].upper()
        status = task['status']
        version = task['version']
        started = task.get('started_at', 'None')[:19] if task.get('started_at') else 'None'
        created = task['created_at'][:19] if task.get('created_at') else 'None'
        completed = task.get('completed_at', 'None')[:19] if task.get('completed_at') else 'None'

        if status in ['pending', 'running']:
            category = "进行中"
            pending_count += 1
        elif status == 'completed' and version == 2 and task.get('started_at'):
            category = "正常 (修复生效)"
            fixed_count += 1
        elif status == 'completed' and version == 1 and not task.get('started_at'):
            category = "孤儿任务 (修复前)"
            orphan_count += 1
        else:
            category = "其他"

        print(f"{i:2}. [{task_type:6}] {status:10} | V{version} | {category}")
        print(f"    ID: {task['external_task_id'][:45]}")
        print(f"    Created:   {created}")
        if status != 'pending':
            print(f"    Started:   {started}")
            print(f"    Completed: {completed}")
        print()

    print("=" * 70)
    print(f"正常任务（V2 + started_at）: {fixed_count}")
    print(f"孤儿任务（V1 + no started_at）: {orphan_count}")
    print(f"进行中任务: {pending_count}")
    print("=" * 70)


if __name__ == "__main__":
    main()
