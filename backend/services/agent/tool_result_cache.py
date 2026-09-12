"""工具结果会话级 TTL 缓存

从 tool_loop_executor.py 拆出（V2.2 §三 500 行红线），
被 ToolLoopExecutor 内部持有，承载读工具的会话级缓存。

设计：
- 缓存资格由 ToolSpec.cacheable 单独声明（保留 code_execute 既有资格）
- 单条结果 > 8000 字符不缓存（防止内存膨胀）
- 缓存条目上限 50 条（满了跳过新增，简单策略）
- TTL 5 分钟，过期条目读取时主动删除
"""

import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple


class ToolResultCache:
    """会话级 ERP 工具结果缓存（每个 ToolLoopExecutor 实例独立持有）"""

    _CACHE_TTL = 300.0  # 5 分钟
    _CACHE_MAX_ENTRIES = 50  # 最多缓存 50 条
    _CACHE_MAX_VALUE_CHARS = 8000  # 单条结果上限

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[Any, float]] = {}

    @staticmethod
    def is_cacheable(tool_name: str) -> bool:
        """Compatibility query of Spec cache eligibility; concurrency is independent."""
        from services.tools.catalog import definition_registry
        spec = definition_registry().get(tool_name)
        return spec.cacheable if spec else False

    @staticmethod
    def _key(tool_name: str, args: Dict[str, Any]) -> str:
        sorted_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
        return f"{tool_name}:{hashlib.md5(sorted_args.encode()).hexdigest()}"

    def get(self, tool_name: str, args: Dict[str, Any]) -> Optional[Any]:
        if not self.is_cacheable(tool_name):
            return None
        key = self._key(tool_name, args)
        entry = self._store.get(key)
        if entry is None:
            return None
        if (time.monotonic() - entry[1]) < self._CACHE_TTL:
            return entry[0]
        # 过期条目删除，释放空间
        del self._store[key]
        return None

    def put(self, tool_name: str, args: Dict[str, Any], result: Any) -> None:
        if not self.is_cacheable(tool_name):
            return
        from services.tools.result import ToolResult
        if isinstance(result, ToolResult):
            from services.tools.result_payload import encode_result
            if result.execution.status != "succeeded" or result.execution.cancelled:
                return
            result = encode_result(result)
            if len(json.dumps(result, ensure_ascii=False)) > self._CACHE_MAX_VALUE_CHARS:
                return
            if len(self._store) < self._CACHE_MAX_ENTRIES:
                self._store[self._key(tool_name, args)] = (result, time.monotonic())
            return
        # 大小判断：兼容直接调用方也按完整安全载荷衡量，保留原对象返回 API
        from services.agent.agent_result import AgentResult
        if isinstance(result, AgentResult):
            from services.tools.result_payload import _JSONBoundary, _agent_value
            value = _agent_value(result, _JSONBoundary())
            if len(json.dumps(value, ensure_ascii=False)) > self._CACHE_MAX_VALUE_CHARS:
                return
        elif isinstance(result, str):
            if len(result) > self._CACHE_MAX_VALUE_CHARS:
                return
        else:
            # 未知类型不缓存
            return
        # 条目上限，满了跳过（简单策略，单次请求内缓存不会太多）
        if len(self._store) >= self._CACHE_MAX_ENTRIES:
            return
        key = self._key(tool_name, args)
        self._store[key] = (result, time.monotonic())
