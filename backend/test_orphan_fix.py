"""
测试孤儿任务修复效果

使用方法：
1. 从前端发起一次 Chat 对话（输入任意文本，如"你好"）
2. 运行此脚本：python3 test_orphan_fix.py
3. 查看最新任务的 version 和 started_at 是否正确
"""

import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv('.env')
db = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))

print("=" * 70)
print("📊 孤儿任务修复效果测试")
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

    # 分类
    if status in ['pending', 'running']:
        status_icon = "🔄"
        category = "进行中"
        pending_count += 1
    elif status == 'completed' and version == 2 and task.get('started_at'):
        status_icon = "✅"
        category = "正常 (修复生效)"
        fixed_count += 1
    elif status == 'completed' and version == 1 and not task.get('started_at'):
        status_icon = "❌"
        category = "孤儿任务 (修复前)"
        orphan_count += 1
    else:
        status_icon = "⚠️"
        category = "其他"

    print(f"{i:2}. {status_icon} [{task_type:6}] {status:10} | V{version} | {category}")
    print(f"    ID: {task['external_task_id'][:45]}")
    print(f"    Created:   {created}")
    if status != 'pending':
        print(f"    Started:   {started}")
        print(f"    Completed: {completed}")
    print()

# 统计
print("=" * 70)
print("📈 统计结果：")
print("=" * 70)
print(f"✅ 正常任务（V2 + started_at）: {fixed_count}")
print(f"❌ 孤儿任务（V1 + no started_at）: {orphan_count}")
print(f"🔄 进行中任务: {pending_count}")
print()

if fixed_count > 0:
    print("🎉 发现修复后的正常任务！修复已生效！")
elif orphan_count > 0 and pending_count == 0:
    print("⚠️ 只有孤儿任务，没有新任务。请从前端发起一次测试。")
else:
    print("⏳ 等待任务完成...")

print("\n" + "=" * 70)
print("💡 测试步骤：")
print("1. 打开前端: https://everydayai.com.cn")
print("2. 发起 Chat 对话，输入任意文本（如'你好'）")
print("3. 等待5-10秒后重新运行此脚本")
print("=" * 70)
