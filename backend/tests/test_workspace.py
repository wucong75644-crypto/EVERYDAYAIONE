"""
core/workspace.py 单元测试

覆盖：resolve_workspace_dir / resolve_staging_dir
      三种用户场景（企业/个人/无用户）+ 边界值 + 路径与 FileExecutor 对齐
"""

import hashlib
from pathlib import Path

from core.workspace import (
    resolve_workspace_dir,
    resolve_staging_dir,
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

