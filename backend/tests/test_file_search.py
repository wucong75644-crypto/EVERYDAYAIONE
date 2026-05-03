"""
workspace 文件搜索测试

验证 _search_files_sync 纯函数的搜索逻辑：
关键词匹配、隐藏文件跳过、staging 跳过、limit 截断、空目录、大小写不敏感。
"""

import os
import tempfile
from pathlib import Path

import pytest

from api.routes.file import _search_files_sync


def _stub_cdn(rel_path: str) -> str:
    return f"https://cdn.test/{rel_path}"


@pytest.fixture()
def workspace(tmp_path: Path):
    """创建一个模拟 workspace 目录结构"""
    # 根目录文件
    (tmp_path / "report.xlsx").write_text("data")
    (tmp_path / "使用指南.md").write_text("guide")
    (tmp_path / "notes.txt").write_text("note")

    # 子目录
    sub = tmp_path / "exports"
    sub.mkdir()
    (sub / "report_2024.csv").write_text("csv")
    (sub / "summary.pdf").write_text("pdf")

    # 隐藏文件 — 应跳过
    (tmp_path / ".hidden_file.txt").write_text("hidden")
    hidden_dir = tmp_path / ".config"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("secret")

    # staging 目录 — 应跳过
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "temp_report.xlsx").write_text("staging")

    return tmp_path


class TestSearchFilesSync:

    def test_keyword_match(self, workspace: Path):
        """关键词匹配：搜索 'report' 应返回所有含 report 的文件"""
        results = _search_files_sync(workspace, "report", 20, _stub_cdn)
        names = {r["name"] for r in results}
        assert "report.xlsx" in names
        assert "report_2024.csv" in names
        assert len(results) == 2

    def test_case_insensitive_filename(self, workspace: Path):
        """文件名大小写不敏感：小写 keyword 匹配大小写混合的文件名"""
        # _search_files_sync 对 item.name.lower() 做匹配，keyword 由调用方预先转小写
        # 创建一个大写文件名来验证
        (workspace / "MyReport.xlsx").write_text("mixed")
        results = _search_files_sync(workspace, "myreport", 20, _stub_cdn)
        assert len(results) == 1
        assert results[0]["name"] == "MyReport.xlsx"

    def test_chinese_keyword(self, workspace: Path):
        """中文关键词"""
        results = _search_files_sync(workspace, "使用", 20, _stub_cdn)
        assert len(results) == 1
        assert results[0]["name"] == "使用指南.md"

    def test_no_match(self, workspace: Path):
        """无匹配返回空列表"""
        results = _search_files_sync(workspace, "nonexistent", 20, _stub_cdn)
        assert results == []

    def test_limit_truncation(self, workspace: Path):
        """limit 截断：只返回前 N 个结果"""
        # 搜索空字符串会匹配所有文件（keyword in name.lower() 必成立）
        # 改用一个能匹配多个文件的关键词
        results = _search_files_sync(workspace, "report", 1, _stub_cdn)
        assert len(results) == 1

    def test_skip_hidden_files(self, workspace: Path):
        """跳过隐藏文件（.开头）"""
        results = _search_files_sync(workspace, "hidden", 20, _stub_cdn)
        assert len(results) == 0

    def test_skip_hidden_dir_contents(self, workspace: Path):
        """跳过隐藏目录内的文件"""
        results = _search_files_sync(workspace, "secret", 20, _stub_cdn)
        assert len(results) == 0

    def test_skip_staging_dir(self, workspace: Path):
        """跳过 staging 目录内的文件"""
        results = _search_files_sync(workspace, "temp_report", 20, _stub_cdn)
        assert len(results) == 0

    def test_skip_directories(self, workspace: Path):
        """目录本身不应出现在结果中"""
        results = _search_files_sync(workspace, "exports", 20, _stub_cdn)
        assert len(results) == 0

    def test_workspace_path_includes_subdir(self, workspace: Path):
        """子目录文件的 workspace_path 应包含相对路径"""
        results = _search_files_sync(workspace, "report_2024", 20, _stub_cdn)
        assert len(results) == 1
        assert results[0]["workspace_path"] == os.path.join("exports", "report_2024.csv")

    def test_cdn_url_called(self, workspace: Path):
        """cdn_url_fn 被正确调用"""
        results = _search_files_sync(workspace, "notes", 20, _stub_cdn)
        assert len(results) == 1
        assert results[0]["cdn_url"] == "https://cdn.test/notes.txt"

    def test_result_fields(self, workspace: Path):
        """返回字段完整性"""
        results = _search_files_sync(workspace, "notes", 20, _stub_cdn)
        entry = results[0]
        assert "name" in entry
        assert "size" in entry
        assert "modified" in entry
        assert "cdn_url" in entry
        assert "mime_type" in entry
        assert "workspace_path" in entry
        assert isinstance(entry["size"], int)
        assert entry["size"] > 0

    def test_mime_type_detection(self, workspace: Path):
        """MIME 类型自动检测"""
        results = _search_files_sync(workspace, "summary", 20, _stub_cdn)
        assert results[0]["mime_type"] == "application/pdf"

    def test_empty_directory(self, tmp_path: Path):
        """空目录返回空列表"""
        results = _search_files_sync(tmp_path, "anything", 20, _stub_cdn)
        assert results == []
