"""工具结果会话级 TTL 缓存

从 tool_loop_executor.py 拆出（V2.2 §三 500 行红线），
被 ToolLoopExecutor 内部持有，承载读工具的会话级缓存。

设计：
- 仅缓存读工具（concurrency_safe），写工具不缓存
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
        """只缓存读工具（从 chat_tools 的 _CONCURRENT_SAFE_TOOLS 判断）"""
        from config.chat_tools import is_concurrency_safe
        return is_concurrency_safe(tool_name)

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
        # 大小判断：AgentResult 用 summary 长度，str 用自身长度
        from services.agent.agent_result import AgentResult
        if isinstance(result, AgentResult):
            if len(result.summary) > self._CACHE_MAX_VALUE_CHARS:
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
