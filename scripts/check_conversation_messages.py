#!/usr/bin/env python3
"""
检查特定对话的消息内容
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

def check_conversation(conversation_id: str):
    """检查对话的消息"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return

    supabase = create_client(supabase_url, supabase_key)

    # 查询对话信息
    print(f"\n{'='*80}")
    print(f"检查对话: {conversation_id}")
    print(f"{'='*80}")

    # 1. 查询对话基本信息
    conv_result = supabase.table("conversations").select("*").eq("id", conversation_id).execute()
    if conv_result.data:
        conv = conv_result.data[0]
        print(f"\n对话标题: {conv.get('title', 'N/A')}")
        print(f"创建时间: {conv.get('created_at', 'N/A')}")

    # 2. 查询所有消息
    messages_result = supabase.table("messages").select("*").eq("conversation_id", conversation_id).order("created_at").execute()

    if not messages_result.data:
        print("\n❌ 没有找到消息")
        return

    messages = messages_result.data
    print(f"\n找到 {len(messages)} 条消息\n")

    for i, msg in enumerate(messages, 1):
        print(f"\n{'─'*80}")
        print(f"消息 #{i}")
        print(f"{'─'*80}")
        print(f"ID: {msg['id']}")
        print(f"角色: {msg['role']}")
        print(f"状态: {msg.get('status', 'N/A')}")
        print(f"创建时间: {msg['created_at']}")

        # 分析 content
        content = msg.get('content')
        print(f"\nContent 类型: {type(content)}")

        if isinstance(content, list):
            print(f"Content 长度: {len(content)}")
            for j, item in enumerate(content):
                print(f"\n  Content[{j}]:")
                if isinstance(item, dict):
                    print(f"    类型: {item.get('type', 'N/A')}")
                    if item.get('type') == 'text':
                        text = item.get('text', '')
                        if len(text) > 100:
                            print(f"    文本: {text[:100]}...")
                        else:
                            print(f"    文本: {text}")
                    elif item.get('type') in ['image', 'video']:
                        print(f"    URL: {item.get('url', 'N/A')}")
                        print(f"    Width: {item.get('width', 'N/A')}")
                        print(f"    Height: {item.get('height', 'N/A')}")
                else:
                    print(f"    值: {item}")
        elif isinstance(content, str):
            if len(content) > 200:
                print(f"Content: {content[:200]}...")
            else:
                print(f"Content: {content}")
        else:
            print(f"Content: {content}")

        # 检查 generation_params
        gen_params = msg.get('generation_params')
        if gen_params:
            print(f"\nGeneration Params:")
            print(f"  Type: {gen_params.get('type', 'N/A')}")
            print(f"  Model: {gen_params.get('model_id', 'N/A')}")

        # 检查 backend_task_id
        backend_task_id = msg.get('backend_task_id')
        if backend_task_id:
            print(f"\nBackend Task ID: {backend_task_id}")

            # 查询任务状态
            task_result = supabase.table("tasks").select("*").eq("id", backend_task_id).execute()
            if task_result.data:
                task = task_result.data[0]
                print(f"  任务状态: {task.get('status', 'N/A')}")
                print(f"  任务类型: {task.get('task_type', 'N/A')}")

                # 检查任务结果
                task_result_data = task.get('result')
                if task_result_data:
                    print(f"  任务结果类型: {type(task_result_data)}")
                    if isinstance(task_result_data, dict):
                        if 'image_url' in task_result_data:
                            print(f"  图片URL: {task_result_data['image_url']}")
                        if 'video_url' in task_result_data:
                            print(f"  视频URL: {task_result_data['video_url']}")
                        if 'error' in task_result_data:
                            print(f"  错误: {task_result_data['error']}")

    print(f"\n{'='*80}")
    print("检查完成")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    # 从截图中的对话ID
    conversation_id = "6647f44d-39af-422e-b2fd-637349175ab4"

    if len(sys.argv) > 1:
        conversation_id = sys.argv[1]

    check_conversation(conversation_id)
