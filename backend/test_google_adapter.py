"""
Google Gemini Adapter 测试脚本

快速验证 Google adapter 的基本功能。
"""

import asyncio
import os
from services.adapters.google import GoogleChatAdapter


async def test_basic_import():
    """测试 1: 基础导入"""
    print("=" * 60)
    print("测试 1: 基础导入")
    print("=" * 60)

    try:
        from services.adapters.google import GoogleChatAdapter, GoogleClient
        from services.adapters.google.models import GoogleAPIError
        from services.adapters.google.configs import get_model_config
        print("✅ 所有模块导入成功")
        return True
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        return False


async def test_config():
    """测试 2: 模型配置"""
    print("\n" + "=" * 60)
    print("测试 2: 模型配置")
    print("=" * 60)

    try:
        from services.adapters.google.configs import get_model_config

        # 测试 gemini-2.5-flash 配置
        config = get_model_config("gemini-2.5-flash-preview-05-20")
        print(f"✅ 模型配置读取成功")
        print(f"   - 显示名称: {config['display_name']}")
        print(f"   - 上下文窗口: {config['context_window']:,} tokens")
        print(f"   - 最大输出: {config['max_output_tokens']:,} tokens")
        print(f"   - 速率限制: {config['rate_limit_rpm']} RPM")
        print(f"   - 成本: ${config['cost_per_1k_input']}/1k input, ${config['cost_per_1k_output']}/1k output")
        return True
    except Exception as e:
        print(f"❌ 配置读取失败: {e}")
        return False


async def test_adapter_init():
    """测试 3: 适配器初始化"""
    print("\n" + "=" * 60)
    print("测试 3: 适配器初始化")
    print("=" * 60)

    try:
        # 使用测试 API Key（不会真正调用 API）
        adapter = GoogleChatAdapter(
            model_id="gemini-2.5-flash-preview-05-20",
            api_key="test_api_key_for_init"
        )

        print(f"✅ 适配器初始化成功")
        print(f"   - Provider: {adapter.provider}")
        print(f"   - 支持流式: {adapter.supports_streaming}")
        print(f"   - 模型 ID: {adapter._model_id}")

        await adapter.close()
        return True
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_message_conversion():
    """测试 4: 消息格式转换"""
    print("\n" + "=" * 60)
    print("测试 4: 消息格式转换")
    print("=" * 60)

    try:
        adapter = GoogleChatAdapter(
            model_id="gemini-2.5-flash-preview-05-20",
            api_key="test_api_key"
        )

        # 测试纯文本消息
        openai_messages = [
            {"role": "user", "content": "Hello, world!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"}
        ]

        google_messages = await adapter._convert_to_google_format(openai_messages)

        print("✅ 消息格式转换成功")
        print(f"   - 输入消息数: {len(openai_messages)}")
        print(f"   - 输出消息数: {len(google_messages)}")
        print(f"   - 第一条消息角色: {google_messages[0]['role']}")
        print(f"   - 第二条消息角色: {google_messages[1]['role']} (assistant→model)")

        await adapter.close()
        return True
    except Exception as e:
        print(f"❌ 消息转换失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_cost_estimation():
    """测试 5: 成本估算"""
    print("\n" + "=" * 60)
    print("测试 5: 成本估算")
    print("=" * 60)

    try:
        adapter = GoogleChatAdapter(
            model_id="gemini-2.5-flash-preview-05-20",
            api_key="test_api_key"
        )

        cost = adapter.estimate_cost_unified(
            input_tokens=1000,
            output_tokens=500
        )

        print("✅ 成本估算成功")
        print(f"   - 模型: {cost.model}")
        print(f"   - 估算成本: ${cost.estimated_cost_usd}")
        print(f"   - 估算积分: {cost.estimated_credits}")
        print(f"   - 备注: {cost.breakdown.get('note', 'N/A')}")

        await adapter.close()
        return True
    except Exception as e:
        print(f"❌ 成本估算失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_factory_integration():
    """测试 6: 工厂模式集成"""
    print("\n" + "=" * 60)
    print("测试 6: 工厂模式集成")
    print("=" * 60)

    try:
        from services.adapters.factory import create_chat_adapter, MODEL_REGISTRY

        # 检查模型注册
        if "gemini-2.5-flash" not in MODEL_REGISTRY:
            print("❌ 模型未在注册表中")
            return False

        config = MODEL_REGISTRY["gemini-2.5-flash"]
        print(f"✅ 模型注册表检查成功")
        print(f"   - 模型 ID: {config.model_id}")
        print(f"   - Provider: {config.provider}")
        print(f"   - Provider 模型: {config.provider_model}")

        # 注意：这里会因为缺少 GOOGLE_API_KEY 而失败，但这是预期的
        print("\n   注意: 实际创建适配器需要有效的 GOOGLE_API_KEY")
        return True
    except Exception as e:
        print(f"❌ 工厂集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有测试"""
    print("\n" + "🚀 Google Gemini Adapter 测试开始")
    print("=" * 60)

    results = []

    # 运行测试
    results.append(("基础导入", await test_basic_import()))
    results.append(("模型配置", await test_config()))
    results.append(("适配器初始化", await test_adapter_init()))
    results.append(("消息格式转换", await test_message_conversion()))
    results.append(("成本估算", await test_cost_estimation()))
    results.append(("工厂模式集成", await test_factory_integration()))

    # 汇总结果
    print("\n" + "=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")

    print("=" * 60)
    print(f"总计: {passed}/{total} 测试通过")

    if passed == total:
        print("🎉 所有测试通过！")
    else:
        print(f"⚠️  {total - passed} 个测试失败")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
