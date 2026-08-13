"""Runtime owner for the channel-specific streaming ModelLoop bridge."""

from __future__ import annotations

from typing import Any


class RuntimeChatModelLoop:
    """Owns one conversational ModelLoop advancement.

    The conversation actor only supplies a claim and a protocol adapter.  The
    streaming compatibility implementation remains in the chat package so
    its wire format is unchanged, but the lifecycle owner is Runtime.
    """

    async def run(
        self,
        *,
        handler: Any,
        request: Any,
        prepared: Any,
        cancellation_event: Any,
        sink: Any,
        totals: Any,
        blocks: list[dict[str, Any]],
        runtime_state: Any,
    ) -> None:
        from services.handlers.chat.execution_engine import (
            _execute_model_turns,
        )

        await _execute_model_turns(
            handler=handler,
            request=request,
            prepared=prepared,
            cancellation_event=cancellation_event,
            sink=sink,
            totals=totals,
            blocks=blocks,
            runtime_state=runtime_state,
        )
