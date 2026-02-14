"""
完整前后端流程测试
测试从 API 发送聊天请求到 Google 模型并接收响应
"""

import asyncio
import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://localhost:8000"

async def test_full_flow():
    """测试完整流程"""
    print("=" * 80)
    print("🚀 完整前后端流程测试")
    print("=" * 80)

    # 假设的测试用户 token（你需要替换为真实的 token）
    # 或者先登录获取 token
    test_token = os.getenv("TEST_USER_TOKEN", "")

    if not test_token:
        print("⚠️  警告: 未设置 TEST_USER_TOKEN，将尝试不带认证请求")
        print("   如果需要认证，请设置环境变量 TEST_USER_TOKEN")

    headers = {}
    if test_token:
        headers["Authorization"] = f"Bearer {test_token}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. 测试健康检查
        print("\n📌 步骤 1: 测试后端健康检查")
        try:
            response = await client.get(f"{BASE_URL}/health")
            print(f"✅ 后端健康检查: {response.status_code}")
            print(f"   响应: {response.json()}")
        except Exception as e:
            print(f"❌ 健康检查失败: {e}")
            return False

        # 2. 测试发送聊天消息到 Google 模型
        print("\n📌 步骤 2: 发送聊天消息到 Google Gemini 2.5 Flash")

        chat_payload = {
            "model_id": "gemini-2.5-flash",
            "messages": [
                {
                    "role": "user",
                    "content": "用一句话介绍 Python 编程语言"
                }
            ],
            "stream": True  # 测试流式响应
        }

        print(f"📤 请求负载: {json.dumps(chat_payload, ensure_ascii=False, indent=2)}")

        try:
            # 发送流式请求
            response = await client.post(
                f"{BASE_URL}/api/v1/chat",
                json=chat_payload,
                headers=headers
            )

            print(f"📥 响应状态码: {response.status_code}")

            if response.status_code == 200:
                print("✅ 请求成功")

                # 如果是流式响应
                if chat_payload["stream"]:
                    print("\n📡 流式响应内容:")
                    print("-" * 80)

                    full_response = ""
                    async for line in response.aiter_lines():
                        if line.strip():
                            # SSE 格式: data: {...}
                            if line.startswith("data: "):
                                data = line[6:]  # 移除 "data: " 前缀
                                if data == "[DONE]":
                                    print("\n" + "-" * 80)
                                    print("✅ 流式响应完成")
                                    break

                                try:
                                    chunk = json.loads(data)
                                    if "content" in chunk and chunk["content"]:
                                        print(chunk["content"], end="", flush=True)
                                        full_response += chunk["content"]
                                except json.JSONDecodeError:
                                    pass

                    print(f"\n\n✅ 完整响应: {full_response}")
                    print(f"   响应长度: {len(full_response)} 字符")
                else:
                    # 非流式响应
                    result = response.json()
                    print(f"✅ 完整响应: {json.dumps(result, ensure_ascii=False, indent=2)}")

                print("\n" + "=" * 80)
                print("🎉 完整流程测试成功！")
                print("=" * 80)
                return True
            else:
                print(f"❌ 请求失败: {response.status_code}")
                print(f"   错误信息: {response.text}")
                return False

        except Exception as e:
            print(f"❌ 请求异常: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False


async def main():
    """运行测试"""
    success = await test_full_flow()
    exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
