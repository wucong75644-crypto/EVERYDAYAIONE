"""
schema 智能过滤注入 单元测试

覆盖：
- schema_filter 过滤准确性（embedding / LLM / 兜底）
- _filter_by_embedding 真实余弦相似度逻辑
- _filter_by_llm mock HTTP 调用
- registry 扩展（schema_text / embedding / last_used / precompute_embedding）
- LRU 淘汰（条目级 + 对话级）
- from_snapshot 向后兼容
- 对话级 registry 缓存
- _inject_schema_context 上下文注入
- _save_schema_to_conversation 注册端
- _register_result_files schema_text 透传
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.agent.session_file_registry import (
    SessionFileRegistry,
    _MAX_ENTRIES,
    _MAX_CONVERSATIONS,
    get_conversation_registry,
    save_conversation_registry,
    remove_conversation_registry,
    _conversation_registries,
    _conversation_access_time,
)
from services.agent.schema_filter import (
    _cosine_similarity,
    _parse_llm_response,
    filter_schemas,
    _SIMILARITY_THRESHOLD,
)
from services.agent.tool_output import FileRef, ColumnMeta


# ============================================================
# 辅助工厂
# ============================================================


def _make_file_ref(filename: str = "trade_123.parquet", **kwargs) -> FileRef:
    defaults = dict(
        path=f"/staging/{filename}",
        filename=filename,
        format="parquet",
        row_count=100,
        size_bytes=4096,
        columns=[ColumnMeta(name="amount", dtype="numeric", label="金额")],
        preview="amount\n100.5",
        created_at=time.time(),
    )
    defaults.update(kwargs)
    return FileRef(**defaults)


# ============================================================
# SessionFileRegistry 扩展测试
# ============================================================


class TestRegistrySchemaExtension:
    """B1: registry 新增字段测试"""

    def test_register_with_schema_text(self):
        reg = SessionFileRegistry()
        ref = _make_file_ref()
        returned_key = reg.register("trade", "local_db_export", ref, schema_text="文件: trade.parquet\n100行")
        # register() 返回生成的 key
        assert returned_key.startswith("trade:local_db_export:")
        entries = reg.get_schema_entries()
        assert len(entries) == 1
        key, file_ref, schema_text, embedding = entries[0]
        assert key == returned_key
        assert schema_text == "文件: trade.parquet\n100行"
        assert embedding is None  # 未预计算
        assert file_ref is ref

    def test_register_without_schema_text(self):
        reg = SessionFileRegistry()
        reg.register("trade", "tool", _make_file_ref())
        assert reg.get_schema_entries() == []

    def test_set_embedding(self):
        reg = SessionFileRegistry()
        ref = _make_file_ref()
        reg.register("trade", "tool", ref, schema_text="schema")
        key = list(reg._files.keys())[0]
        emb = [0.1, 0.2, 0.3]
        reg.set_embedding(key, emb)
        entries = reg.get_schema_entries()
        assert entries[0][3] == emb

    def test_touch_updates_last_used(self):
        reg = SessionFileRegistry()
        reg.register("trade", "tool", _make_file_ref(), schema_text="s")
        key = list(reg._files.keys())[0]
        old_ts = reg._last_used[key]
        time.sleep(0.01)
        reg.touch(key)
        assert reg._last_used[key] > old_ts

    def test_get_recent_schema_entries(self):
        reg = SessionFileRegistry()
        for i in range(5):
            ref = _make_file_ref(f"file_{i}.parquet")
            reg.register("trade", f"tool_{i}", ref, schema_text=f"schema_{i}")
        # 最后注册的 last_used 最大
        recent = reg.get_recent_schema_entries(2)
        assert len(recent) == 2
        assert recent[0][2] == "schema_4"
        assert recent[1][2] == "schema_3"


class TestRegistryLRU:
    """B1: LRU 淘汰测试"""

    def test_lru_evicts_oldest(self):
        reg = SessionFileRegistry()
        for i in range(_MAX_ENTRIES + 5):
            ref = _make_file_ref(f"file_{i}.parquet")
            reg.register("trade", f"tool_{i}", ref, schema_text=f"schema_{i}")
        assert len(reg._files) == _MAX_ENTRIES
        assert len(reg._schemas) <= _MAX_ENTRIES

    def test_lru_preserves_recent(self):
        reg = SessionFileRegistry()
        refs = []
        for i in range(_MAX_ENTRIES + 3):
            ref = _make_file_ref(f"file_{i}.parquet")
            reg.register("trade", f"tool_{i}", ref, schema_text=f"schema_{i}")
            refs.append(ref)
        # 最后注册的应该存活
        last_ref = refs[-1]
        assert any(r.filename == last_ref.filename for _, r in reg.list_all())


class TestRegistrySnapshot:
    """B1: 序列化/反序列化 + 向后兼容"""

    def test_roundtrip_with_schema(self):
        reg = SessionFileRegistry()
        ref = _make_file_ref()
        reg.register("trade", "tool", ref, schema_text="my schema")
        key = list(reg._files.keys())[0]
        reg.set_embedding(key, [0.1, 0.2, 0.3])

        snapshot = reg.to_snapshot()
        restored = SessionFileRegistry.from_snapshot(snapshot)

        assert len(restored._files) == 1
        entries = restored.get_schema_entries()
        assert len(entries) == 1
        assert entries[0][2] == "my schema"
        assert entries[0][3] == [0.1, 0.2, 0.3]

    def test_from_snapshot_old_format(self):
        """旧 snapshot 缺 schema_text / embedding / last_used 不报错"""
        old_snapshot = [{
            "key": "trade:tool:1000",
            "file_ref": {
                "path": "/staging/trade.parquet",
                "filename": "trade.parquet",
                "format": "parquet",
                "row_count": 50,
                "size_bytes": 1024,
                "columns": [{"name": "amount", "dtype": "numeric", "label": "金额"}],
                "preview": "",
                "created_at": 1000.0,
            },
        }]
        restored = SessionFileRegistry.from_snapshot(old_snapshot)
        assert len(restored._files) == 1
        assert restored.get_schema_entries() == []  # 无 schema
        assert restored._last_used["trade:tool:1000"] == 1000.0  # 降级到 created_at

    def test_from_snapshot_empty(self):
        restored = SessionFileRegistry.from_snapshot([])
        assert len(restored._files) == 0

    def test_from_snapshot_none(self):
        restored = SessionFileRegistry.from_snapshot(None)
        assert len(restored._files) == 0


class TestRegistryMerge:
    """对话级 merge_into 测试"""

    def test_merge_adds_new_entries(self):
        src = SessionFileRegistry()
        src.register("trade", "tool", _make_file_ref("a.parquet"), schema_text="schema_a")
        dst = SessionFileRegistry()
        dst.register("stock", "tool", _make_file_ref("b.parquet"), schema_text="schema_b")

        added = src.merge_into(dst)
        assert len(dst._files) == 2
        assert len(added) == 1  # 只新增了 a.parquet

    def test_merge_does_not_overwrite(self):
        src = SessionFileRegistry()
        src.register("trade", "tool", _make_file_ref("a.parquet"), schema_text="new")
        # dst 已有同 key
        dst = SessionFileRegistry()
        key = list(src._files.keys())[0]
        dst._files[key] = _make_file_ref("a.parquet")
        dst._schemas[key] = "old"

        added = src.merge_into(dst)
        assert dst._schemas[key] == "old"  # 不覆盖
        assert added == []  # key 已存在，不算新增


# ============================================================
# 对话级 registry 缓存测试
# ============================================================


class TestConversationRegistryCache:
    """对话级 registry 缓存"""

    def setup_method(self):
        _conversation_registries.clear()
        _conversation_access_time.clear()

    def test_get_creates_empty(self):
        reg = get_conversation_registry("conv-1")
        assert isinstance(reg, SessionFileRegistry)
        assert len(reg._files) == 0

    def test_get_returns_same(self):
        r1 = get_conversation_registry("conv-1")
        r2 = get_conversation_registry("conv-1")
        assert r1 is r2

    def test_save_merges(self):
        tmp = SessionFileRegistry()
        tmp.register("trade", "tool", _make_file_ref(), schema_text="s")
        added = save_conversation_registry("conv-1", tmp)
        assert len(added) == 1
        reg = get_conversation_registry("conv-1")
        assert len(reg._files) == 1

    def test_remove_clears(self):
        get_conversation_registry("conv-1")
        assert "conv-1" in _conversation_registries
        remove_conversation_registry("conv-1")
        assert "conv-1" not in _conversation_registries
        assert "conv-1" not in _conversation_access_time

    def test_remove_nonexistent_no_error(self):
        remove_conversation_registry("nonexistent")

    def test_lru_evicts_oldest_conversations(self):
        for i in range(_MAX_CONVERSATIONS + 5):
            get_conversation_registry(f"conv-{i}")
        assert len(_conversation_registries) == _MAX_CONVERSATIONS
        # 最早创建的应该被淘汰
        assert "conv-0" not in _conversation_registries
        # 最新创建的应该存活
        assert f"conv-{_MAX_CONVERSATIONS + 4}" in _conversation_registries


# ============================================================
# schema_filter 纯函数测试
# ============================================================


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_similar_vectors(self):
        sim = _cosine_similarity([1, 1, 0], [1, 0, 0])
        assert 0 < sim < 1

    def test_zero_vector(self):
        assert _cosine_similarity([0, 0, 0], [1, 1, 1]) == 0.0


class TestParseLLMResponse:
    def _make_entries(self, n: int):
        return [
            (f"k{i}", _make_file_ref(f"f{i}.parquet"), f"schema_{i}", None)
            for i in range(n)
        ]

    def test_parse_related_files(self):
        entries = self._make_entries(3)
        result = _parse_llm_response("相关文件: 1, 3", entries)
        assert len(result) == 2
        assert result[0][0] == "k0"
        assert result[1][0] == "k2"

    def test_parse_no_related(self):
        entries = self._make_entries(3)
        result = _parse_llm_response("相关文件: 无", entries)
        assert result == []

    def test_parse_garbage(self):
        entries = self._make_entries(3)
        result = _parse_llm_response("我不知道", entries)
        assert result is None

    def test_parse_out_of_range(self):
        entries = self._make_entries(2)
        result = _parse_llm_response("相关文件: 5", entries)
        assert result is None


# ============================================================
# schema_filter 异步过滤测试
# ============================================================


class TestFilterSchemas:
    """filter_schemas 核心逻辑测试。

    注意：≤5 个文件时 filter_schemas 全量注入，不走 embedding/LLM。
    测试 embedding/LLM 路径需要 >5 个文件。_pad_entries 辅助填充到 6+。
    """

    def _make_entry(self, name: str, emb=None):
        ref = _make_file_ref(name)
        return (f"key_{name}", ref, f"schema for {name}", emb)

    def _pad_entries(self, entries: list, target: int = 6, emb=None) -> list:
        """填充到 target 个条目（触发 embedding/LLM 过滤路径）"""
        while len(entries) < target:
            i = len(entries)
            entries.append(self._make_entry(f"_pad_{i}.parquet", emb=emb or [0.0, 0.0, 0.0]))
        return entries

    @pytest.mark.asyncio
    async def test_single_entry_returns_directly(self):
        entry = self._make_entry("a.parquet", emb=[0.1, 0.2])
        result = await filter_schemas("查订单", [entry], [])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_entries(self):
        result = await filter_schemas("查订单", [], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_query(self):
        entry = self._make_entry("a.parquet")
        result = await filter_schemas("", [entry], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_embedding_match(self):
        """有 embedding 且相似度高 → 返回匹配条目（需 >5 文件触发过滤）"""
        high_sim_emb = [1.0, 0.0, 0.0]
        entry1 = self._make_entry("match.parquet", emb=high_sim_emb)
        entry2 = self._make_entry("other.parquet", emb=[0.0, 1.0, 0.0])
        entries = self._pad_entries([entry1, entry2], 6, emb=[0.0, 0.0, 1.0])

        async def _mock_filter(query, all_entries):
            return [(all_entries[0][0], all_entries[0][1], all_entries[0][2])]

        with patch(
            "services.agent.schema_filter._filter_by_embedding",
            new_callable=AsyncMock,
            side_effect=_mock_filter,
        ):
            result = await filter_schemas("查订单", entries, [])
        assert len(result) == 1
        assert result[0][1].filename == "match.parquet"

    @pytest.mark.asyncio
    async def test_no_embedding_falls_to_llm(self):
        """无 embedding → 降级到 LLM（需 >5 文件触发过滤）"""
        entry1 = self._make_entry("a.parquet", emb=None)
        entry2 = self._make_entry("b.parquet", emb=None)
        entries = self._pad_entries([entry1, entry2], 6, emb=None)
        recent = [(entry1[0], entry1[1], entry1[2])]

        with patch(
            "services.agent.schema_filter._filter_by_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await filter_schemas("查数据", entries, recent)
        assert len(result) == 1  # recent fallback

    @pytest.mark.asyncio
    async def test_all_below_threshold_falls_to_llm_then_recent(self):
        """所有 embedding 低于阈值 → LLM 降级 → recent 兜底"""
        low_emb = [0.0, 0.0, 1.0]
        entry1 = self._make_entry("a.parquet", emb=low_emb)
        entry2 = self._make_entry("b.parquet", emb=low_emb)
        entries = self._pad_entries([entry1, entry2], 6, emb=low_emb)
        recent = [(entry1[0], entry1[1], entry1[2])]

        with patch(
            "services.agent.schema_filter._filter_by_embedding",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "services.agent.schema_filter._filter_by_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await filter_schemas("查数据", entries, recent)
        assert len(result) == 1  # recent fallback

    @pytest.mark.asyncio
    async def test_llm_fallback_returns_result(self):
        """embedding 全低 → LLM 成功返回匹配"""
        entry1 = self._make_entry("a.parquet", emb=[0.0, 0.0, 1.0])
        entry2 = self._make_entry("b.parquet", emb=[0.0, 0.0, 1.0])
        entries = self._pad_entries([entry1, entry2], 6, emb=[0.0, 0.0, 1.0])
        llm_match = [(entry2[0], entry2[1], entry2[2])]

        with patch(
            "services.agent.schema_filter._filter_by_embedding",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "services.agent.schema_filter._filter_by_llm",
            new_callable=AsyncMock,
            return_value=llm_match,
        ):
            result = await filter_schemas("查退货", entries, [])
        assert len(result) == 1
        assert result[0][1].filename == "b.parquet"


# ============================================================
# precompute_embedding 测试
# ============================================================


def _mock_compute_embedding(return_value=None, side_effect=None):
    """创建 mock compute_embedding 并注入到 sys.modules 中。

    services.knowledge_config 在测试环境无法直接导入（依赖链太深），
    需要先把模块注入 sys.modules 再 patch。
    """
    import sys
    if "services.knowledge_config" not in sys.modules:
        mock_module = MagicMock()
        sys.modules["services.knowledge_config"] = mock_module
    return patch(
        "services.knowledge_config.compute_embedding",
        new_callable=AsyncMock,
        return_value=return_value,
        side_effect=side_effect,
    )


class TestPrecomputeEmbedding:
    """SessionFileRegistry.precompute_embedding 异步方法"""

    @pytest.mark.asyncio
    async def test_success_sets_embedding(self):
        reg = SessionFileRegistry()
        key = reg.register("trade", "tool", _make_file_ref(), schema_text="schema")
        fake_emb = [0.1] * 1024

        with _mock_compute_embedding(return_value=fake_emb):
            await reg.precompute_embedding(key, "schema text")

        entries = reg.get_schema_entries()
        assert entries[0][3] == fake_emb

    @pytest.mark.asyncio
    async def test_failure_silent_no_embedding(self):
        reg = SessionFileRegistry()
        key = reg.register("trade", "tool", _make_file_ref(), schema_text="schema")

        with _mock_compute_embedding(side_effect=Exception("网络超时")):
            await reg.precompute_embedding(key, "schema text")

        entries = reg.get_schema_entries()
        assert entries[0][3] is None

    @pytest.mark.asyncio
    async def test_none_result_no_embedding(self):
        reg = SessionFileRegistry()
        key = reg.register("trade", "tool", _make_file_ref(), schema_text="s")

        with _mock_compute_embedding(return_value=None):
            await reg.precompute_embedding(key, "text")

        entries = reg.get_schema_entries()
        assert entries[0][3] is None

    @pytest.mark.asyncio
    async def test_nonexistent_key_no_crash(self):
        """key 不存在（可能被 LRU 淘汰）→ 不崩溃"""
        reg = SessionFileRegistry()
        with _mock_compute_embedding(return_value=[0.1]):
            await reg.precompute_embedding("ghost_key", "text")


# ============================================================
# _filter_by_embedding 真实余弦逻辑测试
# ============================================================


class TestFilterByEmbedding:
    """_filter_by_embedding 内部余弦相似度过滤"""

    def _make_entry(self, name, emb):
        ref = _make_file_ref(name)
        return (f"key_{name}", ref, f"schema for {name}", emb)

    @pytest.mark.asyncio
    async def test_high_similarity_matched(self):
        from services.agent.schema_filter import _filter_by_embedding

        entry = self._make_entry("a.parquet", [0.9, 0.1, 0.0])
        with _mock_compute_embedding(return_value=[1.0, 0.0, 0.0]):
            result = await _filter_by_embedding("查订单", [entry])
        assert len(result) == 1
        assert result[0][1].filename == "a.parquet"

    @pytest.mark.asyncio
    async def test_low_similarity_not_matched(self):
        from services.agent.schema_filter import _filter_by_embedding

        entry = self._make_entry("a.parquet", [0.0, 1.0, 0.0])
        with _mock_compute_embedding(return_value=[1.0, 0.0, 0.0]):
            result = await _filter_by_embedding("查订单", [entry])
        assert result == []

    @pytest.mark.asyncio
    async def test_query_embedding_failure_returns_empty(self):
        from services.agent.schema_filter import _filter_by_embedding

        entry = self._make_entry("a.parquet", [1.0, 0.0, 0.0])
        with _mock_compute_embedding(side_effect=Exception("API 错误")):
            result = await _filter_by_embedding("查订单", [entry])
        assert result == []

    @pytest.mark.asyncio
    async def test_query_embedding_none_returns_empty(self):
        from services.agent.schema_filter import _filter_by_embedding

        entry = self._make_entry("a.parquet", [1.0, 0.0, 0.0])
        with _mock_compute_embedding(return_value=None):
            result = await _filter_by_embedding("查订单", [entry])
        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_some_match_some_not(self):
        from services.agent.schema_filter import _filter_by_embedding

        high = self._make_entry("match.parquet", [1.0, 0.0, 0.0])
        low = self._make_entry("miss.parquet", [0.0, 1.0, 0.0])
        with _mock_compute_embedding(return_value=[1.0, 0.0, 0.0]):
            result = await _filter_by_embedding("查", [high, low])
        assert len(result) == 1
        assert result[0][1].filename == "match.parquet"


# ============================================================
# _filter_by_llm mock HTTP 测试
# ============================================================


class TestFilterByLLMIntegration:
    """_filter_by_llm 通过 filter_schemas 三级降级链验证。

    注意：≤5 个文件全量注入，不走 LLM 降级。需 >5 文件测试降级路径。
    """

    def _make_entry(self, name, emb=None):
        ref = _make_file_ref(name)
        return (f"key_{name}", ref, f"schema for {name}", emb)

    def _pad_entries(self, entries, target=6):
        while len(entries) < target:
            i = len(entries)
            entries.append(self._make_entry(f"_pad_{i}.parquet"))
        return entries

    @pytest.mark.asyncio
    async def test_llm_success_returns_matched(self):
        """embedding 无 → LLM 成功 → 返回结果"""
        entry1 = self._make_entry("a.parquet")
        entry2 = self._make_entry("b.parquet")
        entries = self._pad_entries([entry1, entry2])
        llm_match = [(entry2[0], entry2[1], entry2[2])]

        with patch(
            "services.agent.schema_filter._filter_by_llm",
            new_callable=AsyncMock,
            return_value=llm_match,
        ):
            result = await filter_schemas("查退货", entries, [])

        assert len(result) == 1
        assert result[0][1].filename == "b.parquet"

    @pytest.mark.asyncio
    async def test_llm_returns_empty_list(self):
        """LLM 判断无相关 → 返回空列表（不走兜底）"""
        entry1 = self._make_entry("a.parquet")
        entry2 = self._make_entry("b.parquet")
        entries = self._pad_entries([entry1, entry2])

        with patch(
            "services.agent.schema_filter._filter_by_llm",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await filter_schemas("完全无关", entries, [])

        assert result == []

    @pytest.mark.asyncio
    async def test_llm_failure_falls_to_recent(self):
        """LLM 失败（返回 None）→ 走 recent 兜底"""
        entry1 = self._make_entry("a.parquet")
        entry2 = self._make_entry("b.parquet")
        entries = self._pad_entries([entry1, entry2])
        recent = [(entry1[0], entry1[1], entry1[2])]

        with patch(
            "services.agent.schema_filter._filter_by_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await filter_schemas("查数据", entries, recent)

        assert len(result) == 1  # recent 兜底


# ============================================================
# _inject_schema_context 测试
# ============================================================


# ============================================================
# _inject_schema_context 行为测试
# 直接复现 mixin 方法的逻辑（handlers 导入链在轻量测试环境不可用），
# 验证 registry → filter → messages 注入 的完整数据流。
# ============================================================


class TestInjectSchemaContextBehavior:
    """_inject_schema_context 核心行为验证（不导入 handlers 包）"""

    def setup_method(self):
        _conversation_registries.clear()
        _conversation_access_time.clear()

    @pytest.mark.asyncio
    async def test_injects_schema_system_message(self):
        """有匹配 schema → 注入 system 消息"""
        conv_reg = get_conversation_registry("conv-1")
        ref = _make_file_ref("trade_100.parquet")
        key = conv_reg.register("trade", "tool", ref, schema_text="文件: trade\n100行 3列")

        messages = [{"role": "user", "content": "查订单"}]

        # 复现 _inject_schema_context 逻辑
        schema_entries = conv_reg.get_schema_entries()
        assert len(schema_entries) == 1

        recent_entries = conv_reg.get_recent_schema_entries(3)
        matched = [(key, ref, "文件: trade\n100行 3列")]

        # 注入
        lines = ["[可用数据文件 schema]", ""]
        for k, r, text in matched:
            lines.append(f"=== {r.filename} ===")
            lines.append(text)
            lines.append("")
            conv_reg.touch(k)

        schema_prompt = "\n".join(lines).rstrip()
        messages.insert(0, {"role": "system", "content": schema_prompt})

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "[可用数据文件 schema]" in messages[0]["content"]
        assert "trade_100.parquet" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_empty_registry_no_entries(self):
        """registry 为空 → get_schema_entries 返回空"""
        conv_reg = get_conversation_registry("conv-empty")
        assert conv_reg.get_schema_entries() == []

    @pytest.mark.asyncio
    async def test_filter_empty_result_no_injection(self):
        """多文件(>5) + 全部不匹配 → filter 返回空"""
        conv_reg = get_conversation_registry("conv-2")
        # 注册 6 个文件触发 embedding/LLM 过滤路径
        for i in range(6):
            conv_reg.register(
                f"domain{i}", f"tool{i}",
                _make_file_ref(f"f{i}.parquet"),
                schema_text=f"schema_{i}",
            )

        schema_entries = conv_reg.get_schema_entries()
        recent_entries = conv_reg.get_recent_schema_entries(3)

        # 无 embedding → 走 LLM → LLM 判断无关 → 返回空
        with patch(
            "services.agent.schema_filter._filter_by_llm",
            new_callable=AsyncMock,
            return_value=[],
        ):
            matched = await filter_schemas("完全无关的闲聊", schema_entries, recent_entries)
        assert matched == []

    @pytest.mark.asyncio
    async def test_touch_updates_on_injection(self):
        """注入时 touch key 更新 last_used"""
        conv_reg = get_conversation_registry("conv-touch")
        key = conv_reg.register("trade", "tool", _make_file_ref(), schema_text="s")
        old_ts = conv_reg._last_used[key]
        time.sleep(0.01)
        conv_reg.touch(key)
        assert conv_reg._last_used[key] > old_ts


# ============================================================
# _save_schema_to_conversation 行为测试
# 验证 register → merge → conversation registry 的完整数据流
# ============================================================


class TestSaveSchemaToConversationBehavior:
    """_save_schema_to_conversation 核心行为验证"""

    def setup_method(self):
        _conversation_registries.clear()
        _conversation_access_time.clear()

    def test_registers_to_conversation_registry(self):
        """file_ref + summary → 对话级 registry 有条目"""
        ref = _make_file_ref("trade_200.parquet")

        # 复现 _save_schema_to_conversation 逻辑
        tmp = SessionFileRegistry()
        tmp.register("trade", "erp_agent", ref, schema_text="文件: trade\n200行")
        added = save_conversation_registry("conv-save", tmp)

        assert len(added) == 1
        reg = get_conversation_registry("conv-save")
        entries = reg.get_schema_entries()
        assert len(entries) == 1
        assert entries[0][2] == "文件: trade\n200行"

    def test_no_summary_registers_file_but_no_schema(self):
        """summary 为空 → 文件注册但无 schema"""
        ref = _make_file_ref("empty.parquet")

        tmp = SessionFileRegistry()
        tmp.register("stock", "tool", ref, schema_text="")
        save_conversation_registry("conv-empty", tmp)

        reg = get_conversation_registry("conv-empty")
        assert len(reg.list_all()) == 1
        assert reg.get_schema_entries() == []

    def test_duplicate_merge_no_double_entry(self):
        """同一个 key 重复 merge → 不重复"""
        ref = _make_file_ref("dup.parquet")
        tmp = SessionFileRegistry()
        tmp.register("trade", "tool", ref, schema_text="schema")

        added1 = save_conversation_registry("conv-dup", tmp)
        added2 = save_conversation_registry("conv-dup", tmp)

        assert len(added1) == 1
        assert len(added2) == 0  # 第二次不新增
        reg = get_conversation_registry("conv-dup")
        assert len(reg.list_all()) == 1


# ============================================================
# _register_result_files schema_text 透传测试
# ============================================================


class TestRegisterResultFilesSchemaPassthrough:
    """tool_loop_executor._register_result_files 透传 schema_text"""

    def test_schema_text_from_summary(self):
        """result.summary → registry schema_text"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_loop_executor import ToolLoopExecutor

        registry = SessionFileRegistry()
        executor = ToolLoopExecutor.__new__(ToolLoopExecutor)
        executor._file_registry = registry
        executor._collected_files = []

        ref = _make_file_ref("test.parquet")
        result = MagicMock()
        result.file_ref = ref
        result.source = "trade"
        result.summary = "文件: test\n50行 2列"
        result.collected_files = None

        executor._register_result_files(result, "local_db_export")

        entries = registry.get_schema_entries()
        assert len(entries) == 1
        assert entries[0][2] == "文件: test\n50行 2列"

    def test_no_summary_no_schema(self):
        """result.summary 为空 → 不存 schema"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_loop_executor import ToolLoopExecutor

        registry = SessionFileRegistry()
        executor = ToolLoopExecutor.__new__(ToolLoopExecutor)
        executor._file_registry = registry
        executor._collected_files = []

        ref = _make_file_ref("test.parquet")
        result = MagicMock()
        result.file_ref = ref
        result.source = "trade"
        result.summary = ""
        result.collected_files = None

        executor._register_result_files(result, "tool")

        assert registry.get_schema_entries() == []
        assert len(registry.list_all()) == 1  # 文件仍注册

    def test_no_file_ref_skips_registration(self):
        """result 无 file_ref → 不注册"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_loop_executor import ToolLoopExecutor

        registry = SessionFileRegistry()
        executor = ToolLoopExecutor.__new__(ToolLoopExecutor)
        executor._file_registry = registry
        executor._collected_files = []

        result = MagicMock()
        result.file_ref = None
        result.collected_files = None

        executor._register_result_files(result, "tool")

        assert len(registry.list_all()) == 0


# ============================================================
# Registry 新增 public 方法（审查修复）
# ============================================================


class TestRegistryPublicMethods:
    """remove / entries_count / get_oldest_keys 公共方法。"""

    def test_remove_clears_all_fields(self):
        """remove 应同时清理 _files/_schemas/_embeddings/_last_used/_access_counts。"""
        registry = SessionFileRegistry()
        ref = _make_file_ref("test.parquet")
        key = registry.register("dom", "tool", ref, schema_text="schema")
        registry.set_embedding(key, [0.1, 0.2])
        # 模拟 access_count（通过 ref.id）
        if ref.id:
            registry._access_counts[ref.id] = 5

        registry.remove(key)

        assert key not in registry._files
        assert key not in registry._schemas
        assert key not in registry._embeddings
        assert key not in registry._last_used
        if ref.id:
            assert ref.id not in registry._access_counts

    def test_remove_nonexistent_key_noop(self):
        """删除不存在的 key 不报错。"""
        registry = SessionFileRegistry()
        registry.remove("nonexistent:key:123")
        assert registry.entries_count() == 0

    def test_entries_count(self):
        """entries_count 返回正确数量。"""
        registry = SessionFileRegistry()
        assert registry.entries_count() == 0

        for i in range(5):
            registry.register(f"d{i}", "t", _make_file_ref(f"f{i}.parquet"))
        assert registry.entries_count() == 5

    def test_get_oldest_keys_order(self):
        """get_oldest_keys 按 last_used 升序。"""
        import time as _time
        registry = SessionFileRegistry()

        keys = []
        for i in range(5):
            k = registry.register(f"d{i}", "t", _make_file_ref(f"f{i}.parquet"))
            keys.append(k)
            _time.sleep(0.01)  # 确保 last_used 有差异

        # touch 第一个，让它变成"最近使用"
        registry.touch(keys[0])

        oldest = registry.get_oldest_keys(2)
        # 第一个被 touch 过，不应该在最旧的 2 个里
        assert keys[0] not in oldest
        assert len(oldest) == 2

    def test_get_oldest_keys_more_than_total(self):
        """请求数量超过总数时返回全部。"""
        registry = SessionFileRegistry()
        registry.register("d", "t", _make_file_ref("f.parquet"))
        oldest = registry.get_oldest_keys(10)
        assert len(oldest) == 1


# ============================================================
# _pending_schemas 收集协议测试
# ============================================================


class TestPendingSchemaProtocol:
    """data_query / fetch_all_pages → _pending_schemas → registry 全链路"""

    def test_register_schemas_from_tools_creates_entries(self):
        """_register_schemas_from_tools 将 pending 条目写入 registry"""
        from services.handlers.chat_tool_mixin import ChatToolMixin
        from services.agent.session_file_registry import (
            get_conversation_registry, _conversation_registries,
        )

        conv_id = "test_pending_schema_001"
        _conversation_registries.pop(conv_id, None)

        pending = [
            ("利润表.xlsx", "/tmp/利润表.xlsx", "利润表.xlsx | 307行 × 10列\n列: 店铺名(text), 实付金额(float)"),
            ("运营分组.xlsx", "/tmp/运营分组.xlsx", "运营分组.xlsx | 108行 × 3列\n列: 运营(text), 店铺名(text)"),
        ]

        ChatToolMixin._register_schemas_from_tools(conv_id, pending)

        registry = get_conversation_registry(conv_id)
        entries = registry.get_schema_entries()
        assert len(entries) == 2
        schemas = [e[2] for e in entries]
        assert any("利润表" in s for s in schemas)
        assert any("运营分组" in s for s in schemas)

        _conversation_registries.pop(conv_id, None)

    def test_register_schemas_empty_pending(self):
        """空 pending 不报错"""
        from services.handlers.chat_tool_mixin import ChatToolMixin
        from services.agent.session_file_registry import _conversation_registries

        conv_id = "test_pending_empty"
        _conversation_registries.pop(conv_id, None)
        ChatToolMixin._register_schemas_from_tools(conv_id, [])
        _conversation_registries.pop(conv_id, None)


class TestFilterSchemasFullInject:
    """≤5 个文件时全量注入，跳过相似度过滤"""

    def _make_entry(self, name: str, emb=None):
        ref = _make_file_ref(name)
        return (f"key_{name}", ref, f"schema for {name}", emb)

    @pytest.mark.asyncio
    async def test_two_files_full_inject(self):
        """2 个文件 → 全量注入，不走 embedding"""
        entries = [self._make_entry("a.parquet"), self._make_entry("b.parquet")]
        result = await filter_schemas("任意查询", entries, [])
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_five_files_full_inject(self):
        """5 个文件 → 全量注入"""
        entries = [self._make_entry(f"f{i}.parquet") for i in range(5)]
        result = await filter_schemas("任意查询", entries, [])
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_six_files_uses_embedding(self):
        """6 个文件 → 走 embedding 过滤"""
        entries = [
            self._make_entry(f"f{i}.parquet", emb=[float(i), 0.0, 0.0])
            for i in range(6)
        ]
        # 不 mock embedding → _filter_by_embedding 会失败 → 降级
        # 降级 LLM 也不可用 → 兜底返回最近3个
        recent = [(entries[0][0], entries[0][1], entries[0][2])]
        result = await filter_schemas("查询", entries, recent)
        # 不应该是 6 个全量（那是 ≤5 的行为）
        assert len(result) < 6
