"""
多租户 Phase 1-8 自动化验证脚本

验证：DB 迁移 → 企业 CRUD → 企业登录 → 数据隔离 → ERP 工具过滤 → 配置加解密

用法：
  cd backend && source venv/bin/activate
  python scripts/verify_multi_tenant.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Python path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import get_settings
from core.crypto import aes_encrypt, aes_decrypt, generate_encrypt_key
from core.security import create_access_token, hash_password

settings = get_settings()
PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
results = []


def report(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, ok))
    msg = f"  {status} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def get_db():
    """获取同步 Supabase 客户端"""
    from core.database import get_db as _get_db
    return _get_db()


# ============================================================
# 1. DB 迁移验证
# ============================================================


def verify_db_migration():
    print("\n=== 1. DB 迁移验证 ===")
    db = get_db()

    # 新表
    for table in ["organizations", "org_members", "org_configs", "org_invitations"]:
        try:
            r = db.table(table).select("*").limit(0).execute()
            report(f"表 {table} 存在", True)
        except Exception as e:
            report(f"表 {table} 存在", False, str(e))

    # org_id 列
    for table in ["conversations", "tasks", "credits_history", "credit_transactions",
                   "erp_products", "erp_document_items"]:
        try:
            r = db.table(table).select("org_id").limit(0).execute()
            report(f"{table}.org_id 列", True)
        except Exception as e:
            report(f"{table}.org_id 列", False, str(e))

    # users.current_org_id
    try:
        r = db.table("users").select("current_org_id").limit(0).execute()
        report("users.current_org_id 列", True)
    except Exception as e:
        report("users.current_org_id 列", False, str(e))


# ============================================================
# 2. AES 加解密
# ============================================================


def verify_crypto():
    print("\n=== 2. AES-256-GCM 加解密 ===")

    key = settings.org_config_encrypt_key
    report("ORG_CONFIG_ENCRYPT_KEY 已配置", bool(key))
    if not key:
        return

    try:
        plaintext = "test-api-key-12345"
        encrypted = aes_encrypt(plaintext, key)
        decrypted = aes_decrypt(encrypted, key)
        report("加密→解密 roundtrip", decrypted == plaintext)
    except Exception as e:
        report("加密→解密 roundtrip", False, str(e))

    try:
        wrong_key = generate_encrypt_key()
        aes_decrypt(encrypted, wrong_key)
        report("错误密钥拒绝", False, "应抛异常但没有")
    except ValueError:
        report("错误密钥拒绝", True)
    except Exception as e:
        report("错误密钥拒绝", False, str(e))


# ============================================================
# 3. 企业 CRUD + 成员管理
# ============================================================


def verify_org_crud():
    print("\n=== 3. 企业 CRUD ===")
    db = get_db()

    from services.org.org_service import OrgService
    svc = OrgService(db)

    # 找一个超管用户
    admin_result = db.table("users").select("id, phone").eq("role", "super_admin").limit(1).execute()
    if not admin_result.data:
        report("找到超管用户", False, "无 super_admin 用户")
        return None
    admin_id = str(admin_result.data[0]["id"])
    report("找到超管用户", True, f"id={admin_id}")

    # 创建测试企业
    test_org_name = "__verify_test_org__"
    try:
        # 清理旧测试数据
        db.table("organizations").delete().eq("name", test_org_name).execute()
    except Exception:
        pass

    try:
        org = svc.create_organization(test_org_name, admin_id)
        org_id = str(org["id"])
        report("创建企业", True, f"org_id={org_id}")
    except Exception as e:
        report("创建企业", False, str(e))
        return None

    # 查询企业
    try:
        org_info = svc.get_organization(org_id)
        report("查询企业", org_info["name"] == test_org_name)
    except Exception as e:
        report("查询企业", False, str(e))

    # 列出成员
    try:
        members = svc.list_members(org_id, admin_id)
        report("列出成员", len(members) == 1, f"count={len(members)}")
    except Exception as e:
        report("列出成员", False, str(e))

    # 用户企业列表
    try:
        orgs = svc.list_user_organizations(admin_id)
        found = any(o["org_id"] == org_id for o in orgs)
        report("用户企业列表", found)
    except Exception as e:
        report("用户企业列表", False, str(e))

    return org_id, admin_id


# ============================================================
# 4. 企业登录
# ============================================================


def verify_org_login(org_name: str, admin_phone: str):
    print("\n=== 4. 企业密码登录 ===")
    db = get_db()

    from services.auth_service import AuthService
    auth_svc = AuthService(db)

    # 先确认用户有密码（如果没有则跳过）
    user = db.table("users").select("password_hash").eq("phone", admin_phone).single().execute()
    if not user.data or not user.data.get("password_hash"):
        report("企业登录", False, f"用户 {admin_phone} 未设置密码，跳过登录测试")
        return

    # 企业登录（需要真实密码，这里只验证接口不报错）
    try:
        result = asyncio.get_event_loop().run_until_complete(
            auth_svc.login_by_org_password(org_name, admin_phone, "wrong_password_test")
        )
        report("企业登录（错密码）", False, "应拒绝但通过了")
    except Exception as e:
        # 预期抛 AuthenticationError
        report("企业登录（错密码被拒绝）", "错误" in str(e) or "密码" in str(e), str(e)[:80])


# ============================================================
# 5. 对话数据隔离
# ============================================================


def verify_conversation_isolation(org_id: str, user_id: str):
    print("\n=== 5. 对话数据隔离 ===")
    db = get_db()

    from services.conversation_service import ConversationService
    conv_svc = ConversationService(db)

    # 创建企业对话
    try:
        org_conv = asyncio.get_event_loop().run_until_complete(
            conv_svc.create_conversation(user_id, title="企业测试对话", org_id=org_id)
        )
        org_conv_id = org_conv["id"]
        report("创建企业对话", True, f"id={org_conv_id}")
    except Exception as e:
        report("创建企业对话", False, str(e))
        return

    # 散客模式查不到企业对话
    try:
        asyncio.get_event_loop().run_until_complete(
            conv_svc.get_conversation(org_conv_id, user_id, org_id=None)
        )
        report("散客看不到企业对话", False, "应 NotFound 但查到了")
    except Exception:
        report("散客看不到企业对话", True)

    # 企业模式能查到
    try:
        result = asyncio.get_event_loop().run_until_complete(
            conv_svc.get_conversation(org_conv_id, user_id, org_id=org_id)
        )
        report("企业模式查到自己的对话", result["id"] == org_conv_id)
    except Exception as e:
        report("企业模式查到自己的对话", False, str(e))

    # 清理
    try:
        asyncio.get_event_loop().run_until_complete(
            conv_svc.delete_conversation(org_conv_id, user_id, org_id=org_id)
        )
    except Exception:
        pass


# ============================================================
# 6. 企业配置（OrgConfigResolver）
# ============================================================


def verify_org_config(org_id: str, user_id: str):
    print("\n=== 6. 企业配置加密存储 ===")
    db = get_db()

    from services.org.config_resolver import OrgConfigResolver
    resolver = OrgConfigResolver(db)

    # 写入配置
    try:
        resolver.set(org_id, "test_verify_key", "test_verify_value", updated_by=user_id)
        report("写入企业配置", True)
    except Exception as e:
        report("写入企业配置", False, str(e))
        return

    # 读取配置
    try:
        val = resolver.get(org_id, "test_verify_key")
        report("读取企业配置（解密）", val == "test_verify_value", f"value={val}")
    except Exception as e:
        report("读取企业配置（解密）", False, str(e))

    # 列出 keys
    try:
        keys = resolver.list_keys(org_id)
        report("列出配置 keys", "test_verify_key" in keys)
    except Exception as e:
        report("列出配置 keys", False, str(e))

    # 散客拿不到企业配置（降级到系统默认）
    try:
        val = resolver.get(None, "test_verify_key")
        report("散客拿不到企业配置", val is None or val != "test_verify_value")
    except Exception as e:
        report("散客拿不到企业配置", False, str(e))

    # 清理
    try:
        resolver.delete(org_id, "test_verify_key")
    except Exception:
        pass


# ============================================================
# 7. ERP 工具过滤
# ============================================================


def verify_tool_filtering():
    print("\n=== 7. ERP 工具过滤 ===")

    from services.tool_executor import ToolExecutor
    from unittest.mock import MagicMock

    # 散客不应有 ERP 工具
    personal_executor = ToolExecutor(db=MagicMock(), user_id="test", conversation_id="test", org_id=None)
    erp_tools = [t for t in personal_executor._handlers if "erp" in t and t != "erp_api_search"]
    report("散客无 ERP 工具", len(erp_tools) == 0, f"found: {erp_tools}")

    # 企业有 ERP 工具
    org_executor = ToolExecutor(db=MagicMock(), user_id="test", conversation_id="test", org_id="org-test")
    erp_tools = [t for t in org_executor._handlers if "erp" in t or "local_" in t]
    report("企业有 ERP 工具", len(erp_tools) > 0, f"count={len(erp_tools)}")


# ============================================================
# 清理 + 汇总
# ============================================================


def cleanup(org_id: str):
    """清理测试数据"""
    db = get_db()
    try:
        db.table("org_members").delete().eq("org_id", org_id).execute()
        db.table("organizations").delete().eq("id", org_id).execute()
    except Exception:
        pass


def main():
    print("=" * 60)
    print("  多租户 Phase 1-8 自动化验证")
    print("=" * 60)

    # 1. DB 迁移
    verify_db_migration()

    # 2. 加解密
    verify_crypto()

    # 3. 企业 CRUD
    result = verify_org_crud()
    if not result:
        print("\n❌ 企业创建失败，后续验证跳过")
        return

    org_id, admin_id = result

    # 4. 企业登录
    admin_phone = get_db().table("users").select("phone").eq("id", admin_id).single().execute().data["phone"]
    verify_org_login("__verify_test_org__", admin_phone)

    # 5. 对话隔离
    verify_conversation_isolation(org_id, admin_id)

    # 6. 配置加密
    verify_org_config(org_id, admin_id)

    # 7. 工具过滤
    verify_tool_filtering()

    # 清理测试数据
    cleanup(org_id)

    # 汇总
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed

    print("\n" + "=" * 60)
    print(f"  验证完成: {passed}/{total} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        print("\n失败项:")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
        sys.exit(1)
    else:
        print("\n✅ 全部验证通过，多租户 Phase 1-8 就绪")


if __name__ == "__main__":
    main()
