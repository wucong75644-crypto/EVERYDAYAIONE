#!/usr/bin/env python3
"""
批量创建测试消息脚本

用于生成大量测试消息，验证虚拟滚动和懒加载功能。
使用方法：
    python3 create_test_messages.py --conversation-id <对话ID> --count 200
"""

import asyncio
import argparse
from datetime import datetime
from supabase import create_client
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # 使用 service_role key 跳过 RLS


async def create_test_messages(conversation_id: str, user_id: str, count: int):
    """批量创建测试消息"""

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("错误：请确保 .env 文件中配置了 SUPABASE_URL 和 SUPABASE_SERVICE_ROLE_KEY")
        return

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"开始创建 {count} 条测试消息...")

    messages = []
    for i in range(count):
        # 交替创建用户消息和AI消息
        if i % 2 == 0:
            # 用户消息
            message = {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "user",
                "content": f"这是测试消息 #{i+1}，用于验证消息加载性能。",
                "credits_cost": 0,
            }
        else:
            # AI消息（有些带图片）
            has_image = i % 10 == 1  # 每10条消息有1条带图片
            content = f"这是AI的回复消息 #{i+1}。" + ("包含一张示例图片。" if has_image else "纯文本回复。")

            message = {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "assistant",
                "content": content,
                "image_url": "https://picsum.photos/400/300" if has_image else None,
                "credits_cost": 5,
            }

        messages.append(message)

        # 每50条提交一次（避免单次请求过大）
        if len(messages) >= 50:
            try:
                supabase.table("messages").insert(messages).execute()
                print(f"已创建 {i+1}/{count} 条消息")
                messages = []
            except Exception as e:
                print(f"创建消息失败: {e}")
                return

    # 提交剩余消息
    if messages:
        try:
            supabase.table("messages").insert(messages).execute()
            print(f"已创建 {count}/{count} 条消息")
        except Exception as e:
            print(f"创建消息失败: {e}")
            return

    print("✅ 测试消息创建完成！")
    print(f"\n测试统计：")
    print(f"  - 总消息数: {count}")
    print(f"  - 用户消息: {count // 2}")
    print(f"  - AI消息: {count // 2}")
    print(f"  - 带图片消息: {count // 10}")
    print(f"\n现在可以刷新页面查看加载效果。")


def main():
    parser = argparse.ArgumentParser(description="批量创建测试消息")
    parser.add_argument(
        "--conversation-id",
        type=str,
        required=True,
        help="对话ID（从浏览器URL获取）",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        required=True,
        help="用户ID（从数据库获取）",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=200,
        help="要创建的消息数量（默认200条）",
    )

    args = parser.parse_args()

    # 运行异步函数
    asyncio.run(create_test_messages(
        conversation_id=args.conversation_id,
        user_id=args.user_id,
        count=args.count,
    ))


if __name__ == "__main__":
    main()
