"""
file_path_cache.FileEntry.analyzed 测试

覆盖 P0 新增的 set_analyzed / is_analyzed —— 这是 <attachments> XML
status 字段切换"未分析 → 已分析"的跨轮持久标记。
"""

from services.agent.file_path_cache import FilePathCache


class TestSetIsAnalyzed:

    def test_default_unanalyzed(self):
        """新注册的文件默认 analyzed=False"""
        cache = FilePathCache()
        cache.register("sales.xlsx", workspace="/abs/sales.xlsx")
        assert cache.is_analyzed("sales.xlsx") is False

    def test_set_true_then_query(self):
        """set_analyzed(True) 后 is_analyzed 返回 True"""
        cache = FilePathCache()
        cache.register("sales.xlsx", workspace="/abs/sales.xlsx")
        cache.set_analyzed("sales.xlsx", True)
        assert cache.is_analyzed("sales.xlsx") is True

    def test_set_default_true(self):
        """set_analyzed 默认参数为 True"""
        cache = FilePathCache()
        cache.register("sales.xlsx", workspace="/abs/sales.xlsx")
        cache.set_analyzed("sales.xlsx")  # 不传第二个参数
        assert cache.is_analyzed("sales.xlsx") is True

    def test_can_toggle_back_to_false(self):
        """set_analyzed(False) 可以重置回未分析（虽实际场景不常用）"""
        cache = FilePathCache()
        cache.register("sales.xlsx", workspace="/abs/sales.xlsx")
        cache.set_analyzed("sales.xlsx", True)
        cache.set_analyzed("sales.xlsx", False)
        assert cache.is_analyzed("sales.xlsx") is False

    def test_unregistered_file_returns_false(self):
        """未注册文件查询 is_analyzed → False（无静默失败）"""
        cache = FilePathCache()
        assert cache.is_analyzed("never_registered.xlsx") is False

    def test_set_on_unregistered_no_throw(self):
        """对未注册文件调 set_analyzed 不抛异常（静默忽略）"""
        cache = FilePathCache()
        cache.set_analyzed("not_yet.xlsx", True)
        # 查询仍然 False，因为没有 FileEntry
        assert cache.is_analyzed("not_yet.xlsx") is False

    def test_normalized_name_matching(self):
        """归一化匹配：注册 '销售 报表.xlsx'，用 '销售报表.xlsx' 查询也能命中"""
        cache = FilePathCache()
        cache.register("销售 报表.xlsx", workspace="/abs/销售报表.xlsx")
        cache.set_analyzed("销售 报表.xlsx", True)
        # 归一化后两者等价
        assert cache.is_analyzed("销售报表.xlsx") is True

    def test_multiple_files_independent_state(self):
        """多文件 analyzed 状态相互独立"""
        cache = FilePathCache()
        cache.register("a.xlsx", workspace="/abs/a.xlsx")
        cache.register("b.xlsx", workspace="/abs/b.xlsx")
        cache.register("c.xlsx", workspace="/abs/c.xlsx")

        cache.set_analyzed("a.xlsx", True)
        cache.set_analyzed("c.xlsx", True)

        assert cache.is_analyzed("a.xlsx") is True
        assert cache.is_analyzed("b.xlsx") is False
        assert cache.is_analyzed("c.xlsx") is True

    def test_register_does_not_reset_analyzed(self):
        """重复 register 同一文件不应该重置 analyzed 状态
        （上传 → analyze → 用户再次 @引用 重新注册，状态应保留）"""
        cache = FilePathCache()
        cache.register("data.xlsx", workspace="/abs/data.xlsx")
        cache.set_analyzed("data.xlsx", True)
        # 模拟下一轮 @ 引用重新注册（这是 chat_context_mixin 的真实行为）
        cache.register("data.xlsx", workspace="/abs/data.xlsx")
        assert cache.is_analyzed("data.xlsx") is True
