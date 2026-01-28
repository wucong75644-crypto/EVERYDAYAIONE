#!/usr/bin/env python3
"""
Supabase 密钥验证脚本
验证新密钥是否可用
"""

import os
import sys
from supabase import create_client, Client

def verify_supabase_connection():
    """验证 Supabase 连接"""
    print("🔍 验证 Supabase 密钥...")
    print("")

    # 从 .env 读取
    env_path = "/Users/wucong/EVERYDAYAIONE/backend/.env"

    url = None
    service_key = None

    try:
        with open(env_path, 'r') as f:
            for line in f:
                if line.startswith('SUPABASE_URL='):
                    url = line.split('=', 1)[1].strip()
                elif line.startswith('SUPABASE_SERVICE_ROLE_KEY='):
                    service_key = line.split('=', 1)[1].strip()
    except Exception as e:
        print(f"❌ 读取 .env 文件失败: {e}")
        return False

    if not url or not service_key:
        print("❌ 未找到 Supabase 配置")
        return False

    # 测试连接
    try:
        print(f"📡 URL: {url}")
        print(f"🔑 Service Key: {service_key[:20]}...")
        print("")

        supabase: Client = create_client(url, service_key)

        # 尝试查询 users 表（只查数量）
        result = supabase.table('users').select('id', count='exact').limit(0).execute()

        print(f"✅ 连接成功！")
        print(f"✅ 数据库响应正常")
        print(f"📊 用户总数: {result.count if hasattr(result, 'count') else '未知'}")
        print("")
        print("🎉 密钥验证通过！")
        return True

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("")
        print("可能的原因：")
        print("  1. 密钥未更新")
        print("  2. 密钥格式错误")
        print("  3. 网络问题")
        return False

if __name__ == "__main__":
    success = verify_supabase_connection()
    sys.exit(0 if success else 1)
