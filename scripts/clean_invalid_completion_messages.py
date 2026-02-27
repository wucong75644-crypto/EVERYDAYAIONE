#!/usr/bin/env python3
"""
清理无效的"生成完成"消息（包括字符串类型的 content）
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "backend"))

from dotenv import load_dotenv
from supabase import create_client

# 加载环境变量
load_dotenv(project_root / "backend" / ".env")

def clean_invalid_messages():
    """清理无效的生成完成消息"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return

    supabase = create_client(supabase_url, supabase_key)

    print(f"\n{'='*80}")
    print("清理无效的'生成完成'消息")
    print(f"{'='*80}\n")

    # 查询所有 assistant 角色的消息
    messages_result = supabase.table("messages").select("*").eq("role", "assistant").execute()

    if not messages_result.data:
        print("❌ 没有找到消息")
        return

    messages = messages_result.data
    print(f"找到 {len(messages)} 条 assistant 消息\n")

    invalid_messages = []

    for msg in messages:
        msg_id = msg['id']
        content = msg.get('content')
        gen_params = msg.get('generation_params')
        backend_task_id = msg.get('backend_task_id')

        is_invalid = False
        reason = ""

        # 检查模式1：content 是字符串 "生成完成"
        if isinstance(content, str) and content.strip() == "生成完成":
            is_invalid = True
            reason = "content 是纯字符串 '生成完成'"

        # 检查模式2：content 是数组但只包含 "生成完成" 文本，没有图片/视频
        elif isinstance(content, list):
            has_only_completion_text = (
                len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
                and content[0].get("text") == "生成完成"
            )

            if has_only_completion_text:
                is_invalid = True
                reason = "content 只包含 '生成完成' 文本，没有媒体URL"

        # 检查模式3：有 generation_params 类型是 image/video，但 content 中没有对应的 URL
        if gen_params and isinstance(gen_params, dict):
            gen_type = gen_params.get('type')
            if gen_type in ['image', 'video']:
                # 检查 content 中是否有对应类型的 URL
                has_media_url = False
                if isinstance(content, list):
                    has_media_url = any(
                        isinstance(c, dict)
                        and c.get("type") == gen_type
                        and c.get("url")
                        for c in content
                    )

                if not has_media_url:
                    is_invalid = True
                    reason = f"generation_params.type={gen_type} 但 content 中没有{gen_type} URL"

        if is_invalid:
            invalid_messages.append({
                'id': msg_id,
                'conversation_id': msg.get('conversation_id'),
                'created_at': msg.get('created_at'),
                'content': content,
                'reason': reason
            })

    print(f"发现 {len(invalid_messages)} 条无效消息\n")

    if not invalid_messages:
        print("✅ 没有发现无效消息，数据正常")
        return

    # 显示无效消息
    for i, msg in enumerate(invalid_messages, 1):
        print(f"{i}. ID: {msg['id']}")
        print(f"   对话: {msg['conversation_id']}")
        print(f"   时间: {msg['created_at']}")
        print(f"   原因: {msg['reason']}")
        print(f"   Content: {msg['content']}")
        print()

    # 确认删除
    print(f"{'='*80}")
    print(f"准备删除 {len(invalid_messages)} 条无效消息")
    print(f"{'='*80}\n")

    # 执行删除
    print("开始删除...")
    deleted_count = 0
    failed_count = 0

    for msg in invalid_messages:
        try:
            supabase.table("messages").delete().eq("id", msg['id']).execute()
            deleted_count += 1
            print(f"✓ 删除消息 {msg['id']}")
        except Exception as e:
            failed_count += 1
            print(f"✗ 删除消息 {msg['id']} 失败: {e}")

    print(f"\n{'='*80}")
    print(f"清理完成")
    print(f"{'='*80}")
    print(f"成功删除: {deleted_count} 条")
    print(f"删除失败: {failed_count} 条")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    clean_invalid_messages()
