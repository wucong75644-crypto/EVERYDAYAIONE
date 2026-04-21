"""ToolLoopExecutor [FILE] 标记提取单元测试

覆盖：_FILE_RE 正则、_execute_tools 中 [FILE] 提取 + 替换、collected_files 累积
"""
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from services.agent.tool_loop_executor import _FILE_RE


# ============================================================
# _FILE_RE 正则匹配
# ============================================================


class TestFileRegex:
    """_FILE_RE 正则匹配 [FILE] 标记"""

    def test_matches_standard_format(self):
        text = "[FILE]https://cdn.example.com/report.xlsx|销售日报.xlsx|application/vnd.openxmlformats|12345[/FILE]"
        match = _FILE_RE.search(text)
        assert match
        assert match.group("url") == "https://cdn.example.com/report.xlsx"
        assert match.group("name") == "销售日报.xlsx"
        assert match.group("mime") == "application/vnd.openxmlformats"
        assert match.group("size") == "12345"

    def test_matches_multiple(self):
        text = (
            "[FILE]https://a.com/1.xlsx|f1.xlsx|app/xlsx|100[/FILE]\n"
            "中间文字\n"
            "[FILE]https://b.com/2.csv|f2.csv|text/csv|200[/FILE]"
        )
        matches = list(_FILE_RE.finditer(text))
        assert len(matches) == 2
        assert matches[0].group("name") == "f1.xlsx"
        assert matches[1].group("name") == "f2.csv"

    def test_no_match_incomplete(self):
        assert not _FILE_RE.search("[FILE]incomplete[/FILE]")
        assert not _FILE_RE.search("plain text")
        assert not _FILE_RE.search("[FILE]url|name|mime[/FILE]")  # 缺 size

    def test_chinese_filename(self):
        text = "[FILE]https://cdn.com/a.xlsx|库存报表_2026年.xlsx|application/vnd|9999[/FILE]"
        match = _FILE_RE.search(text)
        assert match
        assert match.group("name") == "库存报表_2026年.xlsx"

    def test_embedded_in_longer_text(self):
        """[FILE] 标记嵌在大段文本中间"""
        text = (
            "统计完成，共100条记录。\n\n"
            "[FILE]https://cdn.com/r.xlsx|报表.xlsx|app/xlsx|5000[/FILE]\n\n"
            "以上是您要的数据。"
        )
        match = _FILE_RE.search(text)
        assert match
        assert match.group("name") == "报表.xlsx"


# ============================================================
# [FILE] 提取与替换逻辑（模拟 _execute_tools 内的处理）
# ============================================================


class TestFileExtraction:
    """模拟 tool_loop_executor._execute_tools 中的 [FILE] 提取逻辑"""

    def _extract(self, result: str):
        """复现 _execute_tools 中的提取逻辑"""
        collected = []
        if result and "[FILE]" in result:
            for m in _FILE_RE.finditer(result):
                collected.append({
                    "url": m.group("url"),
                    "name": m.group("name"),
                    "mime_type": m.group("mime"),
                    "size": int(m.group("size")),
                })
            result = _FILE_RE.sub(
                lambda m: f"📎 文件: {m.group('name')}", result,
            )
        return result, collected

    def test_single_file_extracted(self):
        text = "生成完成\n[FILE]https://cdn.com/a.xlsx|报表.xlsx|app/xlsx|2048[/FILE]"
        cleaned, files = self._extract(text)
        assert len(files) == 1
        assert files[0]["url"] == "https://cdn.com/a.xlsx"
        assert files[0]["name"] == "报表.xlsx"
        assert files[0]["mime_type"] == "app/xlsx"
        assert files[0]["size"] == 2048
        assert "[FILE]" not in cleaned
        assert "📎 文件: 报表.xlsx" in cleaned

    def test_multiple_files_extracted(self):
        text = (
            "[FILE]https://a.com/1.csv|d.csv|text/csv|100[/FILE]\n"
            "[FILE]https://b.com/2.xlsx|r.xlsx|app/xlsx|200[/FILE]"
        )
        cleaned, files = self._extract(text)
        assert len(files) == 2
        assert files[0]["name"] == "d.csv"
        assert files[1]["name"] == "r.xlsx"
        assert "[FILE]" not in cleaned

    def test_no_file_marker(self):
        text = "普通文本，没有文件标记"
        cleaned, files = self._extract(text)
        assert cleaned == text
        assert files == []

    def test_empty_string(self):
        cleaned, files = self._extract("")
        assert cleaned == ""
        assert files == []

    def test_surrounding_text_preserved(self):
        text = "前缀\n[FILE]https://x.com/f.csv|f.csv|text/csv|50[/FILE]\n后缀"
        cleaned, files = self._extract(text)
        assert cleaned.startswith("前缀\n")
        assert cleaned.endswith("\n后缀")
        assert len(files) == 1


# ============================================================
# collected_files 通过 LoopResult 透传
# ============================================================


class TestCollectedFilesInLoopResult:
    """LoopResult.collected_files 字段正确传递"""

    def test_default_empty(self):
        from services.agent.loop_types import LoopResult
        result = LoopResult(text="ok", total_tokens=0, turns=1, is_llm_synthesis=True)
        assert result.collected_files == []

    def test_with_files(self):
        from services.agent.loop_types import LoopResult
        files = [{"url": "https://x.com/f.xlsx", "name": "f.xlsx", "mime_type": "app/xlsx", "size": 100}]
        result = LoopResult(
            text="ok", total_tokens=0, turns=1,
            is_llm_synthesis=True, collected_files=files,
        )
        assert len(result.collected_files) == 1
        assert result.collected_files[0]["name"] == "f.xlsx"


class TestCollectedFilesInAgentResult:
    """AgentResult.collected_files 字段正确传递（Phase 6: 替代 ERPAgentResult）"""

    def test_default_none(self):
        from services.agent.agent_result import AgentResult
        result = AgentResult(status="success", summary="ok")
        assert result.collected_files is None

    def test_with_files(self):
        from services.agent.agent_result import AgentResult
        files = [{"url": "https://x.com/f.xlsx", "name": "f.xlsx", "mime_type": "app/xlsx", "size": 100}]
        result = AgentResult(status="success", summary="ok", collected_files=files)
        assert len(result.collected_files) == 1
