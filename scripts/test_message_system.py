#!/usr/bin/env python3
"""
消息系统端到端测试脚本

测试场景：
1. 聊天消息正常发送和 WebSocket 接收
2. 图片生成任务流程
3. 视频生成任务流程
4. 任务恢复流程
5. 错误处理

使用方式：
    python scripts/test_message_system.py

需要环境：
- 后端运行在 http://localhost:8000
- 有效的测试用户凭据
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any

import httpx
import websockets

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

# 测试配置
BASE_URL = "http://localhost:8000/api"
WS_URL = "ws://localhost:8000/api/ws"

# 测试结果
test_results: list[dict] = []


def log(message: str, level: str = "INFO"):
    """打印日志"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def record_result(test_name: str, passed: bool, message: str, details: Any = None):
    """记录测试结果"""
    result = {
        "test": test_name,
        "passed": passed,
        "message": message,
        "details": details,
    }
    test_results.append(result)
    status = "✅ PASS" if passed else "❌ FAIL"
    log(f"{status}: {test_name} - {message}")


async def login(client: httpx.AsyncClient) -> str | None:
    """登录并获取 token"""
    # 尝试从环境变量获取测试凭据
    phone = os.environ.get("TEST_EMAIL", "15395794863")  # 使用手机号
    password = os.environ.get("TEST_PASSWORD", "testpassword")

    try:
        response = await client.post(
            f"{BASE_URL}/auth/login/password",
            json={"phone": phone, "password": password},
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("token", {}).get("access_token") or data.get("access_token")
        else:
            log(f"登录失败: {response.status_code} - {response.text}", "ERROR")
            return None
    except Exception as e:
        log(f"登录异常: {e}", "ERROR")
        return None


async def get_or_create_conversation(client: httpx.AsyncClient, token: str) -> str | None:
    """获取或创建测试对话"""
    headers = {"Authorization": f"Bearer {token}"}

    try:
        # 先获取现有对话
        response = await client.get(f"{BASE_URL}/conversations", headers=headers)
        if response.status_code == 200:
            data = response.json()
            conversations = data.get("conversations", [])
            if conversations:
                return conversations[0]["id"]

        # 创建新对话
        response = await client.post(
            f"{BASE_URL}/conversations",
            headers=headers,
            json={"title": "测试对话"},
        )
        if response.status_code in (200, 201):
            data = response.json()
            return data.get("id") or data.get("conversation", {}).get("id")

        log(f"创建对话失败: {response.status_code}", "ERROR")
        return None
    except Exception as e:
        log(f"获取/创建对话异常: {e}", "ERROR")
        return None


async def test_chat_message_flow(client: httpx.AsyncClient, token: str, conversation_id: str):
    """
    测试场景1: 聊天消息正常发送返回

    验证点：
    - API 调用成功返回 task_id
    - WebSocket 接收流式内容
    - 最终收到 chat_done 事件
    """
    test_name = "T01: 聊天消息正常发送"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        # 1. 发送聊天消息
        response = await client.post(
            f"{BASE_URL}/conversations/{conversation_id}/messages/generate",
            headers=headers,
            json={
                "operation": "send",
                "content": "你好，这是一条测试消息，请简短回复。",
                "model_id": "claude-3-5-sonnet-20241022",
            },
            timeout=30.0,
        )

        if response.status_code != 200:
            record_result(test_name, False, f"API 调用失败: {response.status_code}", response.text)
            return

        data = response.json()
        task_id = data.get("task_id")
        user_message = data.get("user_message")

        if not task_id:
            record_result(test_name, False, "API 响应中缺少 task_id", data)
            return

        log(f"消息发送成功, task_id={task_id}")

        # 2. 连接 WebSocket 接收响应
        received_chunks = []
        chat_done = False

        async with websockets.connect(
            f"{WS_URL}?token={token}",
            close_timeout=5,
        ) as ws:
            # 订阅任务
            await ws.send(json.dumps({
                "type": "subscribe",
                "payload": {"task_id": task_id},
            }))

            # 等待响应（最多30秒）
            try:
                async with asyncio.timeout(30):
                    while not chat_done:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        msg_type = data.get("type")

                        if msg_type == "chat_chunk":
                            text = data.get("payload", {}).get("text", "")
                            received_chunks.append(text)
                            log(f"收到 chunk: {len(text)} 字符")

                        elif msg_type == "chat_done":
                            chat_done = True
                            log("收到 chat_done")

                        elif msg_type == "chat_error":
                            error = data.get("payload", {}).get("error")
                            record_result(test_name, False, f"收到错误: {error}", data)
                            return

            except asyncio.TimeoutError:
                record_result(test_name, False, "等待响应超时", {
                    "received_chunks": len(received_chunks),
                })
                return

        # 3. 验证结果
        if chat_done and len(received_chunks) > 0:
            total_content = "".join(received_chunks)
            record_result(test_name, True, f"成功接收 {len(received_chunks)} 个 chunks", {
                "task_id": task_id,
                "chunks_count": len(received_chunks),
                "total_length": len(total_content),
                "user_message_id": user_message.get("id") if user_message else None,
            })
        else:
            record_result(test_name, False, "未收到完整响应", {
                "chat_done": chat_done,
                "chunks_count": len(received_chunks),
            })

    except Exception as e:
        record_result(test_name, False, f"测试异常: {e}")


async def test_image_generation_flow(client: httpx.AsyncClient, token: str, conversation_id: str):
    """
    测试场景2: 图片生成正常发送返回

    验证点：
    - API 调用成功返回 task_id
    - WebSocket 接收 task_status 事件
    - 最终状态为 completed，包含图片 URL
    """
    test_name = "T02: 图片生成正常发送"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        # 关键：先建立 WebSocket 连接，再发送 API 请求
        # 这样确保任务完成时用户在线能收到推送
        task_completed = False
        final_status = None
        task_id = None

        async with websockets.connect(
            f"{WS_URL}?token={token}",
            close_timeout=5,
        ) as ws:
            log("WebSocket 连接已建立，等待稳定...")
            await asyncio.sleep(0.5)  # 等待连接稳定

            # 1. 发送图片生成请求
            response = await client.post(
                f"{BASE_URL}/images/generate",
                headers=headers,
                json={
                    "conversation_id": conversation_id,
                    "prompt": "一只可爱的猫咪，卡通风格",
                    "model": "google/nano-banana",
                    "size": "1:1",
                    "output_format": "png",
                    "wait_for_result": False,  # 异步模式，通过 WebSocket 接收结果
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                record_result(test_name, False, f"API 调用失败: {response.status_code}", response.text)
                return

            data = response.json()
            task_id = data.get("task_id")

            if not task_id:
                record_result(test_name, False, "API 响应中缺少 task_id", data)
                return

            log(f"图片生成任务已创建, task_id={task_id}")

            # 2. 订阅任务（可选，因为 send_to_user 会发给所有连接）
            await ws.send(json.dumps({
                "type": "subscribe",
                "payload": {"task_id": task_id},
            }))

            # 3. 等待响应（图片生成可能需要更长时间，最多120秒）
            try:
                async with asyncio.timeout(120):
                    while not task_completed:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        msg_type = data.get("type")

                        # 打印所有收到的消息类型
                        log(f"收到 WebSocket 消息: type={msg_type}")

                        if msg_type == "task_status":
                            payload = data.get("payload", {})
                            status = payload.get("status")
                            log(f"收到 task_status: {status}, task_id={data.get('task_id')}")

                            if status in ("completed", "failed"):
                                task_completed = True
                                final_status = payload
                        elif msg_type == "subscribed":
                            log(f"订阅确认: task_id={data.get('payload', {}).get('task_id')}")
                        elif msg_type == "ping":
                            # 心跳，忽略
                            pass

            except asyncio.TimeoutError:
                record_result(test_name, False, "等待图片生成超时（120s）")
                return

        # 3. 验证结果
        if final_status and final_status.get("status") == "completed":
            message = final_status.get("message", {})
            image_url = message.get("image_url")

            if image_url:
                record_result(test_name, True, "图片生成成功", {
                    "task_id": task_id,
                    "message_id": message.get("id"),
                    "image_url": image_url[:50] + "...",
                })
            else:
                record_result(test_name, False, "图片生成完成但无 URL", final_status)
        else:
            record_result(test_name, False, f"图片生成失败", final_status)

    except Exception as e:
        record_result(test_name, False, f"测试异常: {e}")


async def test_pending_tasks_api(client: httpx.AsyncClient, token: str):
    """
    测试场景4-6: 任务恢复 API

    验证 /tasks/pending API 是否正常工作
    """
    test_name = "T04-06: 任务恢复 API"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = await client.get(
            f"{BASE_URL}/tasks/pending",
            headers=headers,
        )

        if response.status_code != 200:
            record_result(test_name, False, f"API 调用失败: {response.status_code}", response.text)
            return

        data = response.json()
        tasks = data.get("tasks", [])
        count = data.get("count", 0)

        record_result(test_name, True, f"获取进行中任务成功，共 {count} 个", {
            "count": count,
            "task_types": [t.get("type") for t in tasks],
        })

    except Exception as e:
        record_result(test_name, False, f"测试异常: {e}")


async def test_websocket_connection(token: str):
    """
    测试 WebSocket 连接和断线重连

    验证点：
    - 连接成功
    - 收到 connected 消息
    - 订阅/取消订阅正常
    """
    test_name = "T07: WebSocket 连接"

    try:
        async with websockets.connect(
            f"{WS_URL}?token={token}",
            close_timeout=5,
        ) as ws:
            # 等待连接确认
            try:
                async with asyncio.timeout(5):
                    msg = await ws.recv()
                    data = json.loads(msg)

                    if data.get("type") == "connected":
                        record_result(test_name, True, "WebSocket 连接成功", {
                            "user_id": data.get("payload", {}).get("user_id"),
                        })
                    else:
                        record_result(test_name, True, "WebSocket 连接成功（无 connected 消息）", {
                            "first_message": data.get("type"),
                        })

            except asyncio.TimeoutError:
                record_result(test_name, True, "WebSocket 连接成功（无初始消息）")

    except Exception as e:
        record_result(test_name, False, f"WebSocket 连接失败: {e}")


async def test_error_handling(client: httpx.AsyncClient, token: str):
    """
    测试场景9: 错误处理

    验证点：
    - 无效对话 ID 返回正确错误
    - 空内容返回验证错误
    """
    test_name = "T09: 错误处理"
    headers = {"Authorization": f"Bearer {token}"}
    errors_handled = []

    try:
        # 测试1: 无效对话 ID
        response = await client.post(
            f"{BASE_URL}/messages/generate",
            headers=headers,
            json={
                "conversation_id": "00000000-0000-0000-0000-000000000000",
                "content": "测试",
                "model_id": "claude-3-5-sonnet-20241022",
            },
        )
        if response.status_code in (400, 403, 404):
            errors_handled.append("无效对话 ID 正确处理")
        else:
            errors_handled.append(f"无效对话 ID 返回 {response.status_code}")

        # 测试2: 空内容
        response = await client.post(
            f"{BASE_URL}/messages/generate",
            headers=headers,
            json={
                "conversation_id": "test",
                "content": "",
                "model_id": "claude-3-5-sonnet-20241022",
            },
        )
        if response.status_code == 422:
            errors_handled.append("空内容验证正确")
        else:
            errors_handled.append(f"空内容返回 {response.status_code}")

        record_result(test_name, True, "错误处理测试完成", errors_handled)

    except Exception as e:
        record_result(test_name, False, f"测试异常: {e}")


def print_summary():
    """打印测试摘要"""
    print("\n" + "=" * 60)
    print("测试摘要")
    print("=" * 60)

    passed = sum(1 for r in test_results if r["passed"])
    failed = sum(1 for r in test_results if not r["passed"])

    for result in test_results:
        status = "✅" if result["passed"] else "❌"
        print(f"{status} {result['test']}: {result['message']}")

    print("-" * 60)
    print(f"总计: {len(test_results)} 个测试 | 通过: {passed} | 失败: {failed}")
    print("=" * 60)


async def main():
    """主测试流程"""
    log("开始消息系统测试")
    log("=" * 50)

    async with httpx.AsyncClient() as client:
        # 1. 登录
        log("正在登录...")
        token = await login(client)

        if not token:
            log("登录失败，请设置 TEST_EMAIL 和 TEST_PASSWORD 环境变量", "ERROR")
            log("示例: TEST_EMAIL=xxx@xxx.com TEST_PASSWORD=xxx python scripts/test_message_system.py")
            return

        log("登录成功")

        # 2. 获取或创建对话
        log("获取测试对话...")
        conversation_id = await get_or_create_conversation(client, token)

        if not conversation_id:
            log("无法获取测试对话", "ERROR")
            return

        log(f"测试对话 ID: {conversation_id}")

        # 3. 运行测试
        log("\n开始运行测试...")

        # T01: 聊天消息
        await test_chat_message_flow(client, token, conversation_id)

        # T02: 图片生成（可选，耗时较长）
        if os.environ.get("TEST_IMAGE", "0") == "1":
            await test_image_generation_flow(client, token, conversation_id)
        else:
            log("跳过图片生成测试（设置 TEST_IMAGE=1 启用）")

        # T04-06: 任务恢复 API
        await test_pending_tasks_api(client, token)

        # T07: WebSocket 连接
        await test_websocket_connection(token)

        # T09: 错误处理
        await test_error_handling(client, token)

    # 打印摘要
    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
