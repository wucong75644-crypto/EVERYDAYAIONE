"""
测试 Sentry 配置

运行此脚本验证 Sentry 是否正确配置。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(Path(__file__).parent.parent / ".env")

# 初始化 Sentry
if os.getenv("SENTRY_DSN"):
    import sentry_sdk

    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        environment=os.getenv("ENVIRONMENT", "development"),
    )

    print(f"✅ Sentry initialized")
    print(f"   DSN: {os.getenv('SENTRY_DSN')[:50]}...")
    print(f"   Environment: {os.getenv('ENVIRONMENT')}")
    print()

    # 发送测试消息
    print("📤 Sending test message to Sentry...")
    sentry_sdk.capture_message(
        "Test message from EverydayAI backend",
        level="info",
        extras={
            "test_type": "manual",
            "description": "Testing Sentry integration",
        },
    )
    print("✅ Test message sent!")
    print()

    # 发送测试错误
    print("📤 Sending test error to Sentry...")
    try:
        raise ValueError("This is a test error - please ignore")
    except Exception as e:
        sentry_sdk.capture_exception(e)
    print("✅ Test error sent!")
    print()

    print("🔍 Check your Sentry dashboard:")
    print("   https://sentry.io/organizations/everydayai/projects/")
    print()
    print("You should see 2 new events:")
    print("   1. Test message (INFO level)")
    print("   2. ValueError exception")

else:
    print("❌ SENTRY_DSN not configured in .env")
    print("   Please add SENTRY_DSN to backend/.env")
