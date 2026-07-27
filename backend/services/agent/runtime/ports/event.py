"""RuntimeEvent Store SPI。"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from services.agent.runtime.domain import EventSequence, RuntimeEvent, SessionId


class RuntimeEventPort(Protocol):
    """业务 RPC 写事件；此 SPI 只提供权限受控的严格重放。"""

    def replay(
        self,
        session_id: SessionId,
        after: EventSequence | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """按 Session sequence 严格递增重放。"""
