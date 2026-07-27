"""RuntimeEvent Store SPI。"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from services.agent.runtime.domain import (
    EventSequence,
    RuntimeEvent,
    RuntimeEventDraft,
    SessionId,
)


class RuntimeEventPort(Protocol):
    """事件必须由业务原子边界分配 sequence 并追加。"""

    async def append(self, event: RuntimeEventDraft) -> RuntimeEvent:
        """分配事件 ID/sequence 后追加，调用方不能自行提供 sequence。"""

    def replay(
        self,
        session_id: SessionId,
        after: EventSequence | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """按 Session sequence 严格递增重放。"""
