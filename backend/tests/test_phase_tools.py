"""phase_tools 域工具构建测试（精简版 — Phase1 已删，只测 domain 函数）"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config.phase_tools import build_domain_tools, build_domain_prompt


class TestBuildDomainTools:
    """build_domain_tools 返回正确工具集"""

    def test_erp_returns_tools(self):
        tools = build_domain_tools("erp")
        assert len(tools) > 5
        names = {t["function"]["name"] for t in tools}
        assert "route_to_chat" in names
        assert "ask_user" in names
        assert "code_execute" in names

    def test_erp_contains_erp_tools(self):
        tools = build_domain_tools("erp")
        names = {t["function"]["name"] for t in tools}
        # 至少包含远程 ERP 工具
        erp_names = [n for n in names if n.startswith("erp_")]
        assert len(erp_names) >= 3

    def test_crawler_returns_tools(self):
        tools = build_domain_tools("crawler")
        names = {t["function"]["name"] for t in tools}
        assert "social_crawler" in names
        assert "route_to_chat" in names
        assert "ask_user" in names

    def test_computer_returns_tools(self):
        tools = build_domain_tools("computer")
        names = {t["function"]["name"] for t in tools}
        assert "file_read" in names
        assert "code_execute" in names

    def test_unknown_domain_returns_empty(self):
        assert build_domain_tools("unknown") == []

    def test_chat_domain_returns_empty(self):
        assert build_domain_tools("chat") == []


class TestBuildDomainPrompt:
    """build_domain_prompt 返回提示词"""

    def test_erp_prompt_not_empty(self):
        prompt = build_domain_prompt("erp")
        assert len(prompt) > 100
        assert "工具" in prompt

    def test_crawler_prompt_not_empty(self):
        prompt = build_domain_prompt("crawler")
        assert len(prompt) > 50

    def test_computer_prompt_not_empty(self):
        prompt = build_domain_prompt("computer")
        assert len(prompt) > 50

    def test_unknown_returns_empty(self):
        assert build_domain_prompt("unknown") == ""

    def test_erp_prompt_contains_routing_rules(self):
        prompt = build_domain_prompt("erp")
        assert "route_to_chat" in prompt
