"""
清理占位符消息

删除那些只包含"生成完成"文字且没有 task_id 的消息
这些是前端的占位符遗留，没有实际内容
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import os
import json
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(BACKEND_DIR / '.env')

def main():
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    db = create_client(url, key)

    print("=" * 70)
    print("🧹 清理占位符消息")
    print("=" * 70)

    # 查询所有 assistant 消息
    messages = db.table('messages').select('*').eq('role', 'assistant').order('created_at', desc=True).limit(500).execute()

    to_delete = []

    for msg in messages.data:
        content = msg.get('content')
        task_id = msg.get('task_id')

        # 检查是否是占位符消息：
        # 1. 没有 task_id
        # 2. 内容只有"生成完成"文字
        if not task_id and content:
            try:
                content_obj = json.loads(content) if isinstance(content, str) else content

                # 检查是否只有一个文本部分，且内容是"生成完成"
                if len(content_obj) == 1:
                    part = content_obj[0]
                    if part.get('type') == 'text' and part.get('text', '').strip() == '生成完成':
                        to_delete.append({
                            'id': msg['id'],
                            'conv_id': msg['conversation_id'],
                            'created_at': msg['created_at'][:19]
                        })
            except:
                pass

    print(f"\n找到 {len(to_delete)} 条占位符消息需要清理：\n")

    if not to_delete:
        print("✅ 没有需要清理的消息")
        return

    # 显示前10条
    for i, msg in enumerate(to_delete[:10], 1):
        print(f"{i}. 消息ID: {msg['id'][:36]}")
        print(f"   时间: {msg['created_at']}")

    if len(to_delete) > 10:
        print(f"   ... 还有 {len(to_delete) - 10} 条")

    print("\n" + "=" * 70)
    confirm = input(f"确认删除这 {len(to_delete)} 条占位符消息？(yes/no): ")

    if confirm.lower() != 'yes':
        print("❌ 已取消")
        return

    # 批量删除
    deleted_count = 0
    for msg in to_delete:
        try:
            db.table('messages').delete().eq('id', msg['id']).execute()
            deleted_count += 1
        except Exception as e:
            print(f"❌ 删除失败: {msg['id'][:36]} - {e}")

    print("\n" + "=" * 70)
    print(f"✅ 清理完成！")
    print(f"删除了 {deleted_count} 条占位符消息")
    print("=" * 70)

if __name__ == "__main__":
    main()
