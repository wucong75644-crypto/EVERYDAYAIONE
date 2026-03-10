"""记忆服务 — 用户记忆的 CRUD、对话提取和注入"""

import asyncio
import time
from typing import Any, Dict, List

from loguru import logger

from core.exceptions import AppException, NotFoundError, PermissionDeniedError
from services.memory_config import (
    MAX_MEMORIES_PER_USER,
    MAX_INJECTION_COUNT,
    MEMORY_SEARCH_THRESHOLD,
    MEM0_TIMEOUT,
    _get_mem0,
    _get_cached_memories,
    _set_cached_memories,
    _invalidate_cache,
    format_memory,
    format_memory_list,
    verify_memory_ownership,
)
from services.memory_filter import filter_memories
from services.memory_settings import MemorySettingsService


class MemoryService(MemorySettingsService):
    """记忆服务类（继承设置管理，提供 CRUD + 对话集成）"""

    # ===== 记忆 CRUD =====

    async def get_all_memories(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户所有记忆（带内存缓存）"""
        cached = _get_cached_memories(user_id)
        if cached is not None:
            return cached

        mem0 = await _get_mem0()
        if mem0 is None:
            return []

        try:
            result = await asyncio.wait_for(
                mem0.get_all(user_id=user_id), timeout=MEM0_TIMEOUT
            )
            memories = format_memory_list(result)
            _set_cached_memories(user_id, memories)
            return memories
        except asyncio.TimeoutError:
            logger.error(
                f"mem0.get_all() timed out after {MEM0_TIMEOUT}s | user_id={user_id}"
            )
            raise AppException(
                code="MEMORY_TIMEOUT",
                message="获取记忆超时，请稍后重试",
                status_code=504,
            )
        except Exception as e:
            logger.error(
                f"Error fetching memories | user_id={user_id} | error={e}"
            )
            raise AppException(
                code="MEMORY_FETCH_ERROR",
                message="获取记忆列表失败",
                status_code=500,
            )

    async def add_memory(
        self,
        user_id: str,
        content: str,
        source: str = "manual",
    ) -> List[Dict[str, Any]]:
        """添加记忆，返回所有提取到的记忆列表（Mem0 可能从一句话提取多条）"""
        mem0 = await _get_mem0()
        if mem0 is None:
            raise AppException(
                code="MEMORY_UNAVAILABLE",
                message="记忆功能暂不可用",
                status_code=503,
            )

        count = await self.get_memory_count(user_id)
        if count >= MAX_MEMORIES_PER_USER:
            raise AppException(
                code="MEMORY_LIMIT_REACHED",
                message=f"记忆数量已达上限（{MAX_MEMORIES_PER_USER}条），请先清理旧记忆",
                status_code=400,
            )

        try:
            t0 = time.monotonic()
            result = await asyncio.wait_for(
                mem0.add(
                    messages=[{"role": "user", "content": content}],
                    user_id=user_id,
                    metadata={"source": source},
                ),
                timeout=MEM0_TIMEOUT,
            )
            elapsed = time.monotonic() - t0
            logger.info(
                f"mem0.add() completed | user_id={user_id} | "
                f"elapsed={elapsed:.1f}s | content_len={len(content)}"
            )
            items = result if isinstance(result, list) else result.get("results", [])
            added = []
            for item in items:
                if item.get("event", "") in ("ADD", "UPDATE"):
                    formatted = format_memory(item)
                    formatted["metadata"]["source"] = source
                    added.append(formatted)

            if added:
                _invalidate_cache(user_id)
                logger.info(
                    f"Memory added | user_id={user_id} | source={source} | "
                    f"count={len(added)}"
                )
            return added
        except AppException:
            raise
        except asyncio.TimeoutError:
            logger.error(
                f"mem0.add() timed out after {MEM0_TIMEOUT}s | user_id={user_id}"
            )
            raise AppException(
                code="MEMORY_TIMEOUT",
                message="添加记忆超时，请稍后重试",
                status_code=504,
            )
        except Exception as e:
            logger.error(
                f"Error adding memory | user_id={user_id} | error={e}"
            )
            raise AppException(
                code="MEMORY_ADD_ERROR",
                message="添加记忆失败",
                status_code=500,
            )

    async def update_memory(
        self, memory_id: str, content: str, user_id: str = ""
    ) -> Dict[str, Any]:
        """更新一条记忆（带归属验证）"""
        mem0 = await _get_mem0()
        if mem0 is None:
            raise AppException(
                code="MEMORY_UNAVAILABLE",
                message="记忆功能暂不可用",
                status_code=503,
            )

        if user_id:
            await verify_memory_ownership(mem0, memory_id, user_id)

        try:
            result = await asyncio.wait_for(
                mem0.update(memory_id=memory_id, data=content),
                timeout=MEM0_TIMEOUT,
            )
            if user_id:
                _invalidate_cache(user_id)
            logger.info(f"Memory updated | memory_id={memory_id}")
            return {
                "id": memory_id,
                "memory": content,
                "updated_at": result.get("updated_at") if isinstance(result, dict) else None,
            }
        except (AppException, PermissionDeniedError, NotFoundError):
            raise
        except asyncio.TimeoutError:
            logger.error(
                f"mem0.update() timed out after {MEM0_TIMEOUT}s | memory_id={memory_id}"
            )
            raise AppException(
                code="MEMORY_TIMEOUT",
                message="更新记忆超时，请稍后重试",
                status_code=504,
            )
        except Exception as e:
            logger.error(
                f"Error updating memory | memory_id={memory_id} | error={e}"
            )
            raise AppException(
                code="MEMORY_UPDATE_ERROR",
                message="更新记忆失败",
                status_code=500,
            )

    async def delete_memory(self, memory_id: str, user_id: str = "") -> None:
        """删除一条记忆（带归属验证）"""
        mem0 = await _get_mem0()
        if mem0 is None:
            raise AppException(
                code="MEMORY_UNAVAILABLE",
                message="记忆功能暂不可用",
                status_code=503,
            )

        if user_id:
            await verify_memory_ownership(mem0, memory_id, user_id)

        try:
            await asyncio.wait_for(
                mem0.delete(memory_id=memory_id), timeout=MEM0_TIMEOUT
            )
            if user_id:
                _invalidate_cache(user_id)
            logger.info(f"Memory deleted | memory_id={memory_id}")
        except (AppException, PermissionDeniedError, NotFoundError):
            raise
        except asyncio.TimeoutError:
            logger.error(
                f"mem0.delete() timed out after {MEM0_TIMEOUT}s | memory_id={memory_id}"
            )
            raise AppException(
                code="MEMORY_TIMEOUT",
                message="删除记忆超时，请稍后重试",
                status_code=504,
            )
        except Exception as e:
            logger.error(
                f"Error deleting memory | memory_id={memory_id} | error={e}"
            )
            raise AppException(
                code="MEMORY_DELETE_ERROR",
                message="删除记忆失败",
                status_code=500,
            )

    async def delete_all_memories(self, user_id: str) -> None:
        """清空用户所有记忆"""
        mem0 = await _get_mem0()
        if mem0 is None:
            raise AppException(
                code="MEMORY_UNAVAILABLE",
                message="记忆功能暂不可用，无法清空",
                status_code=503,
            )

        try:
            await asyncio.wait_for(
                mem0.delete_all(user_id=user_id), timeout=MEM0_TIMEOUT
            )
            _invalidate_cache(user_id)
            logger.info(f"All memories deleted | user_id={user_id}")
        except asyncio.TimeoutError:
            logger.error(
                f"mem0.delete_all() timed out after {MEM0_TIMEOUT}s | user_id={user_id}"
            )
            raise AppException(
                code="MEMORY_TIMEOUT",
                message="清空记忆超时，请稍后重试",
                status_code=504,
            )
        except Exception as e:
            logger.error(
                f"Error deleting all memories | user_id={user_id} | error={e}"
            )
            raise AppException(
                code="MEMORY_DELETE_ERROR",
                message="清空记忆失败",
                status_code=500,
            )

    async def get_memory_count(self, user_id: str) -> int:
        """获取用户记忆数量（优先读缓存）"""
        cached = _get_cached_memories(user_id)
        if cached is not None:
            return len(cached)
        mem0 = await _get_mem0()
        if mem0 is None:
            return 0
        try:
            t0 = time.monotonic()
            result = await asyncio.wait_for(
                mem0.get_all(user_id=user_id), timeout=MEM0_TIMEOUT
            )
            elapsed = time.monotonic() - t0
            if elapsed > 2:
                logger.warning(f"mem0.get_all() slow | elapsed={elapsed:.1f}s")
            if not result:
                return 0
            memories = result if isinstance(result, list) else result.get("results", [])
            return len(memories)
        except Exception:
            return 0

    # ===== 对话集成 =====

    async def get_relevant_memories(
        self,
        user_id: str,
        query: str,
        limit: int = MAX_INJECTION_COUNT,
    ) -> List[Dict[str, Any]]:
        """检索与当前对话相关的记忆（两级过滤：Mem0 阈值初筛 + 千问精排）"""
        mem0 = await _get_mem0()
        if mem0 is None:
            return []

        try:
            if not query or query.strip() == "":
                result = await asyncio.wait_for(
                    mem0.get_all(user_id=user_id), timeout=MEM0_TIMEOUT
                )
                memories = format_memory_list(result)
                return memories[:limit]

            # 第一级：Mem0 向量搜索 + 相似度阈值初筛
            search_limit = limit * 3
            result = await asyncio.wait_for(
                mem0.search(
                    query=query, user_id=user_id,
                    limit=search_limit, threshold=MEMORY_SEARCH_THRESHOLD,
                ),
                timeout=MEM0_TIMEOUT,
            )
            memories = format_memory_list(result)
            if not memories:
                return []

            logger.info(
                f"Memory search | user_id={user_id} | "
                f"query={query[:50]} | mem0_returned={len(memories)}"
            )

            # 第二级：千问精排
            t0_filter = time.monotonic()
            filtered = await filter_memories(query, memories)
            filter_latency_ms = int((time.monotonic() - t0_filter) * 1000)
            filtered = filtered[:limit]

            logger.info(
                f"Memory pipeline done | user_id={user_id} | "
                f"searched={len(memories)} -> filtered={len(filtered)}"
            )

            # 记录记忆检索效果信号
            self._record_memory_search_signal(
                user_id=user_id,
                mem0_returned=len(memories),
                filtered_count=len(filtered),
                filter_latency_ms=filter_latency_ms,
                query_length=len(query),
            )

            return filtered
        except Exception as e:
            logger.warning(
                f"Memory search failed, skipping | user_id={user_id} | error={e}"
            )
            return []

    @staticmethod
    def _record_memory_search_signal(
        user_id: str,
        mem0_returned: int,
        filtered_count: int,
        filter_latency_ms: int,
        query_length: int,
    ) -> None:
        """记录记忆检索效果信号到 knowledge_metrics（fire-and-forget）"""

        async def _do_record() -> None:
            try:
                from services.knowledge_service import record_metric
                await record_metric(
                    task_type="memory_search",
                    model_id="mem0",
                    status="success",
                    user_id=user_id,
                    params={
                        "mem0_returned": mem0_returned,
                        "filtered_count": filtered_count,
                        "filter_latency_ms": filter_latency_ms,
                        "query_length": query_length,
                    },
                )
            except Exception as e:
                logger.debug(
                    f"Memory search signal record skipped | error={e}"
                )

        asyncio.create_task(_do_record())

    async def extract_memories_from_conversation(
        self,
        user_id: str,
        messages: List[Dict[str, Any]],
        conversation_id: str,
    ) -> List[Dict[str, Any]]:
        """从对话中提取记忆（由 Mem0 LLM 自动识别关键信息）"""
        mem0 = await _get_mem0()
        if mem0 is None:
            return []

        try:
            result = await asyncio.wait_for(
                mem0.add(
                    messages=messages,
                    user_id=user_id,
                    metadata={
                        "source": "auto",
                        "conversation_id": conversation_id,
                    },
                ),
                timeout=MEM0_TIMEOUT,
            )

            if not result:
                return []

            items = result if isinstance(result, list) else result.get("results", [])
            extracted = []
            for item in items:
                event = item.get("event", "")
                if event in ("ADD", "UPDATE"):
                    extracted.append({
                        "id": item.get("id", ""),
                        "memory": item.get("memory", ""),
                    })

            if extracted:
                _invalidate_cache(user_id)
                logger.info(
                    f"Memories extracted | user_id={user_id} | "
                    f"conversation_id={conversation_id} | count={len(extracted)}"
                )
            return extracted
        except Exception as e:
            logger.warning(
                f"Memory extraction failed | user_id={user_id} | "
                f"conversation_id={conversation_id} | error={e}"
            )
            return []
