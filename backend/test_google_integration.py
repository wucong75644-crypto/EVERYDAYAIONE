#!/usr/bin/env python3
"""
Google Gemini 模型完整集成测试

测试从前端到后端的完整流程：
1. 用户登录获取 token
2. 创建对话
3. 发送消息到 Google Gemini 模型
4. 接收流式响应（通过 WebSocket）
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import httpx
import websockets

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

# 测试配置
BASE_URL = "http://localhost:8000/api"
WS_URL = "ws://localhost:8000/api/ws"


def log(message: str, level: str = "INFO"):
    """打印日志"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


async def login(client: httpx.AsyncClient) -> str | None:
    """登录并获取 token"""
    phone = os.environ.get("TEST_EMAIL", "15395794863")
    password = os.environ.get("TEST_PASSWORD", "testpassword")

    log("开始登录...")
    try:
        response = await client.post(
            f"{BASE_URL}/auth/login/password",
            json={"phone": phone, "password": password},
        )
        if response.status_code == 200:
            data = response.json()
            token = data.get("token", {}).get("access_token") or data.get("access_token")
            log(f"✅ 登录成功，token: {token[:20]}...")
            return token
        else:
            log(f"❌ 登录失败: {response.status_code} - {response.text}", "ERROR")
            return None
    except Exception as e:
        log(f"❌ 登录异常: {e}", "ERROR")
        return None


async def get_or_create_conversation(client: httpx.AsyncClient, token: str) -> str | None:
    """获取或创建测试对话"""
    headers = {"Authorization": f"Bearer {token}"}

    log("获取或创建对话...")
    try:
        # 先获取现有对话
        response = await client.get(f"{BASE_URL}/conversations", headers=headers)
        if response.status_code == 200:
            data = response.json()
            conversations = data.get("conversations", [])
            if conversations:
                conv_id = conversations[0]["id"]
                log(f"✅ 使用现有对话: {conv_id}")
                return conv_id

        # 创建新对话
        response = await client.post(
            f"{BASE_URL}/conversations",
            headers=headers,
            json={"title": "Google Gemini 测试对话"},
        )
        if response.status_code == 200 or response.status_code == 201:
            data = response.json()
            conv_id = data["conversation"]["id"]
            log(f"✅ 创建新对话: {conv_id}")
            return conv_id
        else:
            log(f"❌ 创建对话失败: {response.status_code} - {response.text}", "ERROR")
            return None
    except Exception as e:
        log(f"❌ 获取对话异常: {e}", "ERROR")
        return None


async def test_google_chat(client: httpx.AsyncClient, token: str, conversation_id: str):
    """测试 Google Gemini 聊天"""
    headers = {"Authorization": f"Bearer {token}"}

    log("=" * 80)
    log("🚀 测试 Google Gemini 2.5 Flash 聊天")
    log("=" * 80)

    # 构建请求
    payload = {
        "content": [
            {
                "type": "text",
                "text": "用一句话介绍 Python 编程语言"
            }
        ],
        "model": "gemini-2.5-flash",  # 使用 Google 免费模型
        "operation": "send",
        "client_request_id": f"test-{datetime.now().timestamp()}"
    }

    log(f"📤 发送消息到模型: gemini-2.5-flash")
    log(f"   对话 ID: {conversation_id}")
    log(f"   消息内容: {payload['content'][0]['text']}")

    try:
        # 发送请求
        response = await client.post(
            f"{BASE_URL}/conversations/{conversation_id}/messages/generate",
            headers=headers,
            json=payload,
            timeout=60.0
        )

        log(f"📥 响应状态码: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            log("✅ 请求成功")
            log(f"   消息 ID: {result.get('assistant_message_id', 'N/A')}")
            log(f"   任务 ID: {result.get('task_id', 'N/A')}")

            return True, result
        else:
            log(f"❌ 请求失败: {response.status_code}", "ERROR")
            log(f"   错误详情: {response.text}", "ERROR")
            return False, None

    except Exception as e:
        log(f"❌ 请求异常: {type(e).__name__}: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False, None


async def listen_websocket(token: str, conversation_id: str, task_id: str):
    """监听 WebSocket 实时响应"""
    ws_url = f"{WS_URL}?token={token}"

    log("\n📡 连接 WebSocket...")

    try:
        async with websockets.connect(ws_url) as websocket:
            log("✅ WebSocket 连接成功")

            # 订阅任务
            subscribe_msg = {
                "action": "subscribe",
                "task_id": task_id
            }
            await websocket.send(json.dumps(subscribe_msg))
            log(f"📨 已订阅任务: {task_id}")

            log("\n" + "=" * 80)
            log("🌊 流式响应内容:")
            log("-" * 80)

            full_response = ""
            chunk_count = 0

            # 接收消息
            async for message in websocket:
                try:
                    data = json.loads(message)

                    if data.get("type") == "chunk":
                        content = data.get("content", "")
                        if content:
                            print(content, end="", flush=True)
                            full_response += content
                            chunk_count += 1

                    elif data.get("type") == "task_complete":
                        log("\n" + "-" * 80)
                        log("✅ 任务完成")
                        log(f"   总字符数: {len(full_response)}")
                        log(f"   Chunk 数量: {chunk_count}")
                        log(f"   完整响应: {full_response}")
                        break

                    elif data.get("type") == "error":
                        log(f"\n❌ 任务错误: {data.get('error', 'Unknown error')}", "ERROR")
                        break

                except json.JSONDecodeError:
                    log(f"⚠️  无效 JSON: {message}", "WARN")
                except Exception as e:
                    log(f"❌ 处理消息异常: {e}", "ERROR")

            log("=" * 80)
            return True

    except Exception as e:
        log(f"❌ WebSocket 异常: {type(e).__name__}: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试流程"""
    print("\n" + "=" * 80)
    print("🚀 Google Gemini 完整集成测试")
    print("=" * 80)

    async with httpx.AsyncClient() as client:
        # 1. 登录
        token = await login(client)
        if not token:
            log("❌ 登录失败，测试终止", "ERROR")
            return False

        # 2. 获取/创建对话
        conversation_id = await get_or_create_conversation(client, token)
        if not conversation_id:
            log("❌ 获取对话失败，测试终止", "ERROR")
            return False

        # 3. 发送聊天消息
        success, result = await test_google_chat(client, token, conversation_id)
        if not success:
            log("❌ 发送消息失败，测试终止", "ERROR")
            return False

        task_id = result.get("task_id")
        if not task_id:
            log("❌ 未返回 task_id，测试终止", "ERROR")
            return False

        # 4. 监听 WebSocket 流式响应
        ws_success = await listen_websocket(token, conversation_id, task_id)

        # 汇总结果
        print("\n" + "=" * 80)
        if ws_success:
            print("🎉 测试成功完成！")
            print("=" * 80)
            print("✅ 登录成功")
            print("✅ 创建对话成功")
            print("✅ 发送消息到 Google Gemini 成功")
            print("✅ WebSocket 流式接收成功")
            print("=" * 80)
            return True
        else:
            print("⚠️  测试部分成功")
            print("=" * 80)
            print("✅ 登录成功")
            print("✅ 创建对话成功")
            print("✅ 发送消息成功")
            print("❌ WebSocket 流式接收失败")
            print("=" * 80)
            return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
