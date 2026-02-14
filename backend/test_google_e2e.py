"""
Google Gemini API 端到端测试

实际调用 Google API，验证完整流程。
"""

import asyncio
import os
from dotenv import load_dotenv
from services.adapters.factory import create_chat_adapter


async def test_google_api_integration():
    """测试 Google API 集成（端到端）"""
    print("=" * 80)
    print("🚀 Google Gemini API 端到端测试")
    print("=" * 80)

    # 加载环境变量
    load_dotenv()
    google_api_key = os.getenv("GOOGLE_API_KEY")

    if not google_api_key or google_api_key == "your-google-api-key":
        print("❌ 错误: GOOGLE_API_KEY 未配置或使用的是示例值")
        print("   请在 .env 文件中配置真实的 Google API Key")
        return False

    print(f"✅ API Key 已加载: {google_api_key[:10]}...")

    try:
        # 1. 创建适配器
        print("\n" + "-" * 80)
        print("📦 步骤 1: 创建 Google Chat Adapter")
        print("-" * 80)

        adapter = create_chat_adapter("gemini-2.5-flash")
        print(f"✅ 适配器创建成功")
        print(f"   - Provider: {adapter.provider}")
        print(f"   - Model: {adapter._model_id}")
        print(f"   - 支持流式: {adapter.supports_streaming}")

        # 2. 测试简单文本聊天
        print("\n" + "-" * 80)
        print("💬 步骤 2: 测试简单文本聊天（流式）")
        print("-" * 80)

        messages = [
            {"role": "user", "content": "请用一句话介绍一下 Python 编程语言"}
        ]

        print("📤 发送消息:", messages[0]["content"])
        print("📥 流式响应:")
        print("-" * 80)

        full_response = ""
        chunk_count = 0
        final_tokens = {"input": 0, "output": 0}

        async for chunk in adapter.stream_chat(messages):
            if chunk.content:
                print(chunk.content, end="", flush=True)
                full_response += chunk.content
                chunk_count += 1

            # 捕获 token 使用量
            if chunk.prompt_tokens:
                final_tokens["input"] = chunk.prompt_tokens
            if chunk.completion_tokens:
                final_tokens["output"] = chunk.completion_tokens

        print("\n" + "-" * 80)
        print(f"✅ 流式响应完成")
        print(f"   - 总字符数: {len(full_response)}")
        print(f"   - Chunk 数量: {chunk_count}")
        print(f"   - Input tokens: {final_tokens['input']}")
        print(f"   - Output tokens: {final_tokens['output']}")

        # 3. 测试成本估算
        print("\n" + "-" * 80)
        print("💰 步骤 3: 测试成本估算")
        print("-" * 80)

        cost = adapter.estimate_cost_unified(
            input_tokens=final_tokens["input"],
            output_tokens=final_tokens["output"]
        )

        print(f"✅ 成本估算完成")
        print(f"   - 模型: {cost.model}")
        print(f"   - 估算成本: ${cost.estimated_cost_usd}")
        print(f"   - 估算积分: {cost.estimated_credits}")
        print(f"   - 备注: {cost.breakdown.get('note', 'N/A')}")

        # 4. 测试非流式聊天
        print("\n" + "-" * 80)
        print("💬 步骤 4: 测试非流式聊天")
        print("-" * 80)

        messages_sync = [
            {"role": "user", "content": "说一个数字：42"}
        ]

        print("📤 发送消息:", messages_sync[0]["content"])

        response = await adapter.chat_sync(messages_sync)

        print(f"📥 完整响应: {response.content}")
        print(f"✅ 非流式响应完成")
        print(f"   - 字符数: {len(response.content)}")
        print(f"   - Input tokens: {response.prompt_tokens}")
        print(f"   - Output tokens: {response.completion_tokens}")

        # 5. 清理资源
        print("\n" + "-" * 80)
        print("🧹 步骤 5: 清理资源")
        print("-" * 80)

        await adapter.close()
        print("✅ 资源已释放")

        # 汇总结果
        print("\n" + "=" * 80)
        print("🎉 测试成功完成！")
        print("=" * 80)
        print("✅ Google API 集成正常工作")
        print("✅ 流式响应功能正常")
        print("✅ 非流式响应功能正常")
        print("✅ 成本估算功能正常")
        print("=" * 80)

        return True

    except Exception as e:
        print("\n" + "=" * 80)
        print("❌ 测试失败")
        print("=" * 80)
        print(f"错误类型: {type(e).__name__}")
        print(f"错误信息: {str(e)}")

        import traceback
        print("\n详细错误堆栈:")
        traceback.print_exc()

        return False


async def main():
    """运行测试"""
    success = await test_google_api_integration()
    exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
