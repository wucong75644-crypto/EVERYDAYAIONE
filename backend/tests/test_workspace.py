"""
core/workspace.py 单元测试

覆盖：resolve_workspace_dir / resolve_staging_dir
      resolve_upload_dir / resolve_upload_relpath (P0 新增)
      三种用户场景（企业/个人/无用户）+ 边界值 + 路径与 FileExecutor 对齐
"""

import hashlib
from datetime import datetime
from pathlib import Path

from core.workspace import (
    resolve_staging_dir,
    resolve_upload_dir,
    resolve_upload_relpath,
    resolve_workspace_dir,
)


# ============================================================
# resolve_workspace_dir
# ============================================================

class TestResolveWorkspaceDir:

    def test_org_user(self, tmp_path):
        """企业用户：{base}/org/{org_id}/{user_id}"""
        result = resolve_workspace_dir(str(tmp_path), "user-123", "org-abc")
        assert result == str(tmp_path.resolve() / "org" / "org-abc" / "user-123")

    def test_personal_user(self, tmp_path):
        """个人用户：{base}/personal/{md5[:8]}"""
        user_hash = hashlib.md5("user-456".encode()).hexdigest()[:8]
        result = resolve_workspace_dir(str(tmp_path), "user-456")
        assert result == str(tmp_path.resolve() / "personal" / user_hash)

    def test_no_user(self, tmp_path):
        """无用户：直接返回 base"""
        result = resolve_workspace_dir(str(tmp_path))
        assert result == str(tmp_path.resolve())

    def test_org_without_user_id(self, tmp_path):
        """企业但 user_id 为空：{base}/org/{org_id}/（空字符串作为目录名）"""
        result = resolve_workspace_dir(str(tmp_path), "", "org-abc")
        assert result == str(tmp_path.resolve() / "org" / "org-abc" / "")

    def test_consistent_with_file_executor(self, tmp_path):
        """workspace_dir 与 FileExecutor._root 一致"""
        from services.file_executor import FileExecutor
        fe = FileExecutor(
            workspace_root=str(tmp_path),
            user_id="user-789",
            org_id="org-xyz",
        )
        ws_dir = resolve_workspace_dir(str(tmp_path), "user-789", "org-xyz")
        assert ws_dir == fe.workspace_root


# ============================================================
# resolve_staging_dir
# ============================================================

class TestResolveStagingDir:

    def test_org_user_staging(self, tmp_path):
        """企业用户 staging：{workspace_dir}/staging/{conv_id}"""
        result = resolve_staging_dir(str(tmp_path), "u1", "org1", "conv-abc")
        ws_dir = resolve_workspace_dir(str(tmp_path), "u1", "org1")
        assert result == str(Path(ws_dir) / "staging" / "conv-abc")

    def test_personal_user_staging(self, tmp_path):
        """个人用户 staging"""
        result = resolve_staging_dir(str(tmp_path), "u2", conversation_id="conv-xyz")
        ws_dir = resolve_workspace_dir(str(tmp_path), "u2")
        assert result == str(Path(ws_dir) / "staging" / "conv-xyz")

    def test_default_conversation_id(self, tmp_path):
        """conversation_id 为空时用 default"""
        result = resolve_staging_dir(str(tmp_path), "u1", "org1")
        assert result.endswith("/staging/default")

    def test_none_conversation_id(self, tmp_path):
        """conversation_id 为 None 时用 default"""
        result = resolve_staging_dir(str(tmp_path), "u1", "org1", None)
        assert result.endswith("/staging/default")

    def test_different_users_different_dirs(self, tmp_path):
        """不同用户的 staging 路径不同（用户隔离）"""
        dir_a = resolve_staging_dir(str(tmp_path), "user-A", "org1", "conv1")
        dir_b = resolve_staging_dir(str(tmp_path), "user-B", "org1", "conv1")
        assert dir_a != dir_b

    def test_different_convs_different_dirs(self, tmp_path):
        """同一用户不同会话的 staging 路径不同（会话隔离）"""
        dir_a = resolve_staging_dir(str(tmp_path), "u1", "org1", "conv-1")
        dir_b = resolve_staging_dir(str(tmp_path), "u1", "org1", "conv-2")
        assert dir_a != dir_b
        # 但共享同一个用户 workspace
        assert Path(dir_a).parent == Path(dir_b).parent


# ============================================================
# resolve_upload_dir / resolve_upload_relpath (P0 新增：统一上传链路)
# ============================================================

class TestResolveUploadDir:
    """resolve_upload_dir: 计算用户上传目录绝对路径（按月分桶）"""

    def test_org_user_subdir(self, tmp_path):
        """企业用户：{ws}/org/{org_id}/{user_id}/上传/{YYYY-MM}"""
        result = resolve_upload_dir(
            str(tmp_path), "u1", "org1", now=datetime(2026, 6, 2),
        )
        assert result == str(tmp_path.resolve() / "org" / "org1" / "u1" / "上传" / "2026-06")

    def test_personal_user_uses_hash(self, tmp_path):
        """个人用户：{ws}/personal/{md5_8}/上传/{YYYY-MM}"""
        user_hash = hashlib.md5("u1".encode()).hexdigest()[:8]
        result = resolve_upload_dir(
            str(tmp_path), "u1", None, now=datetime(2026, 6, 2),
        )
        assert result == str(tmp_path.resolve() / "personal" / user_hash / "上传" / "2026-06")

    def test_month_zero_padded(self, tmp_path):
        """月份补零（1 月 → 2026-01）"""
        result = resolve_upload_dir(
            str(tmp_path), "u1", "org1", now=datetime(2026, 1, 15),
        )
        assert result.endswith("/上传/2026-01")

    def test_year_boundary(self, tmp_path):
        """跨年正确（12 月 31 日 → 2025-12）"""
        result = resolve_upload_dir(
            str(tmp_path), "u1", "org1", now=datetime(2025, 12, 31, 23, 59, 59),
        )
        assert result.endswith("/上传/2025-12")

    def test_default_now_uses_current(self, tmp_path):
        """now 不传时按调用时刻"""
        before = datetime.now()
        result = resolve_upload_dir(str(tmp_path), "u1", "org1")
        after = datetime.now()
        valid_months = {
            before.strftime("/上传/%Y-%m"),
            after.strftime("/上传/%Y-%m"),
        }
        assert any(result.endswith(m) for m in valid_months)


class TestResolveUploadRelpath:
    """resolve_upload_relpath: 相对路径前缀（不含 user/org）"""

    def test_returns_simple_prefix(self):
        result = resolve_upload_relpath(
            user_id="u1", org_id="org1", now=datetime(2026, 6, 2),
        )
        assert result == "上传/2026-06"

    def test_independent_of_user_org(self):
        """同月不同用户/组织返回完全相同的相对前缀"""
        a = resolve_upload_relpath(user_id="u1", org_id="org1", now=datetime(2026, 6, 2))
        b = resolve_upload_relpath(user_id="u2", org_id="org2", now=datetime(2026, 6, 2))
        c = resolve_upload_relpath(user_id="u1", org_id=None, now=datetime(2026, 6, 2))
        assert a == b == c == "上传/2026-06"


class TestUploadDirRelpathConsistency:
    """relpath 拼到 workspace_dir 后必须等于 upload_dir（双写 invariant）"""

    def test_org_user_paths_consistent(self, tmp_path):
        now = datetime(2026, 6, 2)
        full = resolve_upload_dir(str(tmp_path), "u1", "org1", now=now)
        ws = resolve_workspace_dir(str(tmp_path), "u1", "org1")
        rel = resolve_upload_relpath(user_id="u1", org_id="org1", now=now)
        assert full == f"{ws}/{rel}"

    def test_personal_user_paths_consistent(self, tmp_path):
        now = datetime(2026, 6, 2)
        full = resolve_upload_dir(str(tmp_path), "u1", None, now=now)
        ws = resolve_workspace_dir(str(tmp_path), "u1", None)
        rel = resolve_upload_relpath(user_id="u1", org_id=None, now=now)
        assert full == f"{ws}/{rel}"

    def test_upload_dir_not_collide_with_staging(self, tmp_path):
        """上传/ 与 staging/ 是同级目录，不能互相包含"""
        upload = resolve_upload_dir(str(tmp_path), "u1", "org1", now=datetime(2026, 6, 2))
        staging = resolve_staging_dir(str(tmp_path), "u1", "org1", "c1")
        assert not upload.startswith(staging)
        assert not staging.startswith(upload)

