"""
security.py refresh token 相关函数单元测试

覆盖：
- create_refresh_token：格式、熵、哈希一致性、过期时间
- hash_refresh_token：确定性、与 create_refresh_token 的哈希一致
- create_token_pair：返回格式、DB 写入、唯一 token 工厂
"""

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from core.security import (
    create_refresh_token,
    create_token_pair,
    hash_refresh_token,
)


# ── create_refresh_token ────────────────────────────────

class TestCreateRefreshToken:

    @pytest.fixture(autouse=True)
    def _mock_settings(self):
        settings = MagicMock()
        settings.jwt_refresh_token_expire_days = 7
        with patch("core.security.get_settings", return_value=settings):
            yield settings

    def test_returns_three_tuple(self):
        raw, token_hash, expires_at = create_refresh_token()
        assert isinstance(raw, str)
        assert isinstance(token_hash, str)
        assert isinstance(expires_at, datetime)

    def test_raw_token_has_sufficient_entropy(self):
        """token_urlsafe(48) 产生 64 字符（256-bit 熵）"""
        raw, _, _ = create_refresh_token()
        assert len(raw) >= 60

    def test_hash_matches_sha256_of_raw(self):
        raw, token_hash, _ = create_refresh_token()
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert token_hash == expected

    def test_expires_at_is_7_days_from_now(self):
        _, _, expires_at = create_refresh_token()
        expected = datetime.now(timezone.utc) + timedelta(days=7)
        delta = abs((expires_at - expected).total_seconds())
        assert delta < 2  # 允许 2 秒误差

    def test_each_call_produces_unique_token(self):
        raw1, hash1, _ = create_refresh_token()
        raw2, hash2, _ = create_refresh_token()
        assert raw1 != raw2
        assert hash1 != hash2


# ── hash_refresh_token ──────────────────────────────────

class TestHashRefreshToken:

    def test_deterministic(self):
        token = "test-token-abc"
        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_matches_hashlib_sha256(self):
        token = "my-refresh-token"
        expected = hashlib.sha256(token.encode()).hexdigest()
        assert hash_refresh_token(token) == expected

    def test_consistent_with_create_refresh_token(self):
        """hash_refresh_token(raw) 应和 create_refresh_token 返回的 hash 一致"""
        with patch("core.security.get_settings") as mock:
            mock.return_value = MagicMock(jwt_refresh_token_expire_days=7)
            raw, token_hash, _ = create_refresh_token()
        assert hash_refresh_token(raw) == token_hash

    def test_different_tokens_produce_different_hashes(self):
        assert hash_refresh_token("token-a") != hash_refresh_token("token-b")


# ── create_token_pair ───────────────────────────────────

class TestCreateTokenPair:

    @pytest.fixture(autouse=True)
    def _mock_settings(self):
        settings = MagicMock()
        settings.jwt_access_token_expire_minutes = 30
        settings.jwt_refresh_token_expire_days = 7
        settings.jwt_secret_key = "test-secret"
        settings.jwt_algorithm = "HS256"
        with patch("core.security.get_settings", return_value=settings):
            yield settings

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        insert_mock = MagicMock()
        insert_mock.execute.return_value = MagicMock(data=[{"id": "rt-1"}])
        db.table.return_value.insert.return_value = insert_mock
        return db

    def test_returns_all_required_fields(self, mock_db):
        result = create_token_pair(mock_db, "user-123")
        assert "access_token" in result
        assert "refresh_token" in result
        assert result["token_type"] == "bearer"
        assert result["expires_in"] == 30 * 60
        assert result["refresh_expires_in"] == 7 * 86400

    def test_inserts_refresh_hash_into_db(self, mock_db):
        result = create_token_pair(mock_db, "user-123")

        mock_db.table.assert_called_with("refresh_tokens")
        insert_call = mock_db.table.return_value.insert
        insert_call.assert_called_once()

        inserted = insert_call.call_args[0][0]
        assert inserted["user_id"] == "user-123"
        assert inserted["token_hash"] == hash_refresh_token(result["refresh_token"])
        assert "expires_at" in inserted

    def test_db_stored_hash_matches_raw_token(self, mock_db):
        """DB 存的 hash 应能通过 hash_refresh_token(raw) 验证"""
        result = create_token_pair(mock_db, "user-xyz")
        inserted = mock_db.table.return_value.insert.call_args[0][0]
        assert inserted["token_hash"] == hash_refresh_token(result["refresh_token"])

    def test_access_token_is_valid_jwt(self, mock_db):
        from jose import jwt
        result = create_token_pair(mock_db, "user-123")
        payload = jwt.decode(
            result["access_token"], "test-secret", algorithms=["HS256"]
        )
        assert payload["sub"] == "user-123"

    def test_each_call_produces_unique_refresh_token(self, mock_db):
        """refresh_token 每次不同（access_token 同秒内 iat 相同所以可能相同）"""
        r1 = create_token_pair(mock_db, "user-1")
        r2 = create_token_pair(mock_db, "user-1")
        assert r1["refresh_token"] != r2["refresh_token"]
