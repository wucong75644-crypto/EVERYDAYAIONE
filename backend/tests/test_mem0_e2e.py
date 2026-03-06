"""
Mem0 端到端集成测试

直接连接真实 Supabase PostgreSQL + DashScope API，验证完整记忆链路：
1. Mem0 初始化
2. 添加记忆
3. 查询所有记忆
4. 搜索相关记忆
5. 删除记忆

运行方式（需要 .env 中配置 SUPABASE_DB_URL 和 DASHSCOPE_API_KEY）：
  python -m pytest backend/tests/test_mem0_e2e.py -v -s
"""

import asyncio
import os
import sys
import uuid

import pytest

# 确保 backend 在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 加载 .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


@pytest.fixture(scope="module")
def test_user_id():
    """生成测试用的唯一 user_id，避免影响真实数据"""
    return f"test-e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestMem0E2E:
    """Mem0 端到端集成测试"""

    @pytest.mark.asyncio
    async def test_01_mem0_initialization(self):
        """测试 Mem0 配置构建"""
        from services.memory_service import _build_mem0_config

        config = _build_mem0_config()
        assert config is not None, "配置构建失败，检查 SUPABASE_DB_URL 和 DASHSCOPE_API_KEY"
        assert config["llm"]["provider"] == "openai"
        assert config["embedder"]["provider"] == "openai"
        assert config["vector_store"]["provider"] == "pgvector"
        print(f"\n✓ 配置构建成功: LLM={config['llm']['config']['model']}, EMB={config['embedder']['config']['model']}")

    @pytest.mark.asyncio
    async def test_02_mem0_async_memory_create(self):
        """测试 AsyncMemory 实例创建（验证 DB 连接）"""
        from mem0 import AsyncMemory
        from services.memory_service import _build_mem0_config

        config = _build_mem0_config()
        mem0 = await AsyncMemory.from_config(config)
        assert mem0 is not None
        print("\n✓ AsyncMemory 实例创建成功（DB 连接正常）")

    @pytest.mark.asyncio
    async def test_03_add_memory(self, test_user_id):
        """测试添加记忆"""
        from mem0 import AsyncMemory
        from services.memory_service import _build_mem0_config

        config = _build_mem0_config()
        mem0 = await AsyncMemory.from_config(config)

        result = await mem0.add(
            messages=[{"role": "user", "content": "我的名字叫张三，我在杭州做设计师"}],
            user_id=test_user_id,
        )

        print(f"\n✓ 添加记忆结果: {result}")
        assert result is not None

    @pytest.mark.asyncio
    async def test_04_get_all_memories(self, test_user_id):
        """测试获取所有记忆"""
        from mem0 import AsyncMemory
        from services.memory_service import _build_mem0_config

        config = _build_mem0_config()
        mem0 = await AsyncMemory.from_config(config)

        result = await mem0.get_all(user_id=test_user_id)
        # Mem0 返回可能是 dict 或 list，统一处理
        memories = result if isinstance(result, list) else result.get("results", [])
        print(f"\n✓ 获取记忆列表: {len(memories)} 条")
        for m in memories:
            if isinstance(m, dict):
                print(f"  - [{m.get('id', '?')[:8]}] {m.get('memory', '?')}")
            else:
                print(f"  - {m}")

        assert len(memories) > 0, "应该至少有一条记忆"

    @pytest.mark.asyncio
    async def test_05_search_memory(self, test_user_id):
        """测试搜索相关记忆"""
        from mem0 import AsyncMemory
        from services.memory_service import _build_mem0_config

        config = _build_mem0_config()
        mem0 = await AsyncMemory.from_config(config)

        result = await mem0.search(
            query="这个人做什么工作的",
            user_id=test_user_id,
        )
        memories = result if isinstance(result, list) else result.get("results", [])
        print(f"\n✓ 搜索记忆结果: {len(memories)} 条")
        for m in memories:
            if isinstance(m, dict):
                print(f"  - [{m.get('id', '?')[:8]}] {m.get('memory', '?')} (score={m.get('score', '?')})")

        assert len(memories) > 0, "应该搜索到相关记忆"

    @pytest.mark.asyncio
    async def test_06_cleanup(self, test_user_id):
        """清理测试数据"""
        from mem0 import AsyncMemory
        from services.memory_service import _build_mem0_config

        config = _build_mem0_config()
        mem0 = await AsyncMemory.from_config(config)

        await mem0.delete_all(user_id=test_user_id)
        result = await mem0.get_all(user_id=test_user_id)
        remaining = result if isinstance(result, list) else result.get("results", [])
        assert len(remaining) == 0, f"清理后应该没有记忆，实际还有 {len(remaining)} 条"
        print("\n✓ 测试数据清理完成")
