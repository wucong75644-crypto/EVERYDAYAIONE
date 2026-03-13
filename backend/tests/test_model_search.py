"""
模型搜索单元测试

覆盖：精确查询、能力搜索、场景搜索、无匹配
"""

from services.model_search import search_models


class TestModelSearch:

    def test_exact_search_by_id(self):
        """精确查询模型 ID"""
        result = search_models("deepseek-v3.2")
        assert "deepseek-v3.2" in result
        assert "描述" in result

    def test_exact_search_case_insensitive(self):
        """模型 ID 大小写不敏感"""
        result = search_models("DeepSeek-V3.2")
        assert "deepseek-v3.2" in result

    def test_capability_search_code(self):
        """按能力标签搜索：code"""
        result = search_models("code")
        assert "匹配" in result or "deepseek" in result.lower()

    def test_capability_search_reasoning(self):
        """按能力标签搜索：reasoning"""
        result = search_models("reasoning")
        assert "匹配" in result

    def test_scenario_search_chinese(self):
        """中文场景搜索：写代码"""
        result = search_models("写代码")
        assert "匹配" in result or "code" in result.lower()

    def test_scenario_search_math(self):
        """中文场景搜索：数学"""
        result = search_models("数学")
        assert "匹配" in result

    def test_scenario_search_image(self):
        """中文场景搜索：看图"""
        result = search_models("看图")
        assert "匹配" in result

    def test_no_match(self):
        """搜索无匹配"""
        result = search_models("zzz_impossible_xyz")
        assert "未找到" in result

    def test_empty_query(self):
        """空查询"""
        result = search_models("")
        assert "请输入" in result

    def test_max_results(self):
        """搜索结果不超过5条"""
        result = search_models("对话")
        entries = [
            line for line in result.split("\n")
            if line.strip().startswith("- ")
        ]
        assert len(entries) <= 5

    def test_search_image_model(self):
        """搜索图片模型"""
        result = search_models("nano-banana")
        assert "nano-banana" in result

    def test_search_video_model(self):
        """搜索视频模型"""
        result = search_models("sora")
        assert "sora" in result
