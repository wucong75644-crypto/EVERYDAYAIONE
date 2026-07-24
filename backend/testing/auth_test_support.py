"""认证测试共享数据构造器。"""

from uuid import uuid4


def auth_user(**overrides) -> dict:
    user = {
        "id": str(uuid4()), "phone": "13800138000",
        "nickname": "测试用户", "credits": 100, "status": "active",
        "role": "user", "password_hash": "password-hash",
        "avatar_url": None, "login_methods": ["phone"],
        "created_at": "2026-07-24T00:00:00+00:00",
    }
    user.update(overrides)
    return user
