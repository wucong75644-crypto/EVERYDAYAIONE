"""
三级递进上下文压缩

参考腾讯 TencentDB-Agent-Memory offload 模块的三级压缩策略。
简化实现：不做 Mermaid 画布（我们的 Agent 单循环架构不需要任务图），
保留核心的三级递进压缩逻辑。

触发阈值：
- Mild (≥50%): 替换旧工具输出为摘要
- Aggressive (≥85%): 删除最旧40%消息
- Emergency (≥95%): 强制压至60%，保留≥4条
"""

from __future__ import annotations

import logging
from typing import Any

from .config import get_memory_config

logger = logging.getLogger(__name__)


class ContextCompressor:
    """三级递进上下文压缩器"""

    def __init__(self):
        self._cfg = get_memory_config()

    async def compress_if_needed(
        self,
        messages: list[dict],
        context_window: int | None = None,
    ) -> list[dict]:
        """
        检查上下文占比，按需压缩。

        Args:
            messages: 完整消息列表 [{role, content, ...}]
            context_window: 上下文窗口大小（token数）

        Returns:
            压缩后的消息列表（可能不变、可能减少）
        """
        cfg = self._cfg
        window = context_window or cfg.compress_context_window
        if not messages or window <= 0:
            return messages

        total_tokens = self._estimate_tokens(messages)
        ratio = total_tokens / window

        if ratio < cfg.compress_mild_threshold:
            return messages  # 不需要压缩

        if ratio >= cfg.compress_emergency_threshold:
            logger.warning(f"Compress: EMERGENCY ({ratio:.1%} of context window)")
            return self._emergency_compress(messages, window)

        if ratio >= cfg.compress_aggressive_threshold:
            logger.warning(f"Compress: AGGRESSIVE ({ratio:.1%} of context window)")
            return self._aggressive_compress(messages)

        logger.info(f"Compress: MILD ({ratio:.1%} of context window)")
        return await self._mild_compress(messages)

    # ============================
    # Token 估算
    # ============================

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """
        启发式 token 估算

        中文 ÷ 1.7 + 其他 ÷ 4（和腾讯 heuristic 模式一致）
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
            other_chars = len(content) - chinese_chars
            total += int(chinese_chars / 1.7 + other_chars / 4)
        return total

    def _estimate_msg_tokens(self, msg: dict) -> int:
        """单条消息 token 估算"""
        return self._estimate_tokens([msg])

    # ============================
    # Mild 压缩：替换工具输出为摘要
    # ============================

    async def _mild_compress(self, messages: list[dict]) -> list[dict]:
        """
        Mild 压缩：找到旧的工具调用结果，替换为短摘要。

        策略：
        - 扫描后70%的消息
        - 找到 tool role 的消息（工具输出）
        - 超过200字的替换为 "[工具输出已压缩] {前100字}..."
        """
        if len(messages) < 4:
            return messages

        result = list(messages)
        scan_start = max(1, len(result) * 3 // 10)  # 跳过前30%

        compressed_count = 0
        for i in range(scan_start, len(result)):
            msg = result[i]
            role = msg.get("role", "")
            content = msg.get("content", "")

            # 只压缩工具输出和超长 assistant 回复
            if role == "tool" and isinstance(content, str) and len(content) > 200:
                result[i] = {
                    **msg,
                    "content": f"[工具输出已压缩] {content[:100]}...",
                }
                compressed_count += 1

            elif role == "assistant" and isinstance(content, str) and len(content) > 2000:
                # 超长 assistant 回复：保留前500+后200
                result[i] = {
                    **msg,
                    "content": f"{content[:500]}\n\n[...中间内容已压缩...]\n\n{content[-200:]}",
                }
                compressed_count += 1

        if compressed_count > 0:
            logger.info(f"Compress mild: compressed {compressed_count} messages")

        return result

    # ============================
    # Aggressive 压缩：删除最旧消息
    # ============================

    def _aggressive_compress(self, messages: list[dict]) -> list[dict]:
        """
        Aggressive 压缩：删除最旧40% token 的消息。

        保留规则：
        - 始终保留 system prompt（messages[0]）
        - 始终保留最近4条消息
        - 从前向后删除，直到释放40% token
        """
        if len(messages) <= 5:
            return messages

        total_tokens = self._estimate_tokens(messages)
        target_remove = int(total_tokens * 0.4)

        # 保留头尾
        system_msg = messages[0] if messages[0].get("role") == "system" else None
        keep_tail = messages[-4:]  # 最近4条
        middle = messages[1:-4] if system_msg else messages[:-4]

        removed_tokens = 0
        keep_middle = []

        for msg in reversed(middle):  # 从新到旧保留
            if removed_tokens >= target_remove:
                keep_middle.insert(0, msg)
            else:
                removed_tokens += self._estimate_msg_tokens(msg)

        result = []
        if system_msg:
            result.append(system_msg)
        result.extend(keep_middle)
        result.extend(keep_tail)

        logger.info(
            f"Compress aggressive: {len(messages)} → {len(result)} messages, "
            f"removed ~{removed_tokens} tokens"
        )
        return result

    # ============================
    # Emergency 压缩：强制瘦身
    # ============================

    def _emergency_compress(self, messages: list[dict], window: int) -> list[dict]:
        """
        Emergency 压缩：强制压至60%，保留≥4条消息。

        从后向前保留消息，直到 token 总量 < 60% window。
        """
        target_tokens = int(window * 0.6)

        system_msg = messages[0] if messages[0].get("role") == "system" else None
        system_tokens = self._estimate_msg_tokens(system_msg) if system_msg else 0

        budget = target_tokens - system_tokens
        kept: list[dict] = []
        used = 0

        # 从最新开始保留
        for msg in reversed(messages[1:] if system_msg else messages):
            msg_tokens = self._estimate_msg_tokens(msg)
            if used + msg_tokens > budget and len(kept) >= 4:
                break
            kept.insert(0, msg)
            used += msg_tokens

        # 至少保留4条
        if len(kept) < 4 and len(messages) > 4:
            kept = list(messages[-4:])

        result = []
        if system_msg:
            result.append(system_msg)
        result.extend(kept)

        logger.warning(
            f"Compress emergency: {len(messages)} → {len(result)} messages, "
            f"~{self._estimate_tokens(result)} tokens"
        )
        return result
