"""Real tool execution boundary with mock identity DB and business handlers."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.agent.tool_executor import ToolExecutor


class IdentityDB:
    def __init__(self, user_id="u1", org_id="o1", **conversation):
        self.user_id, self.org_id = user_id, org_id
        self.active = True
        self.conversation = {"user_id": user_id, "org_id": org_id, "scope_type": "user", **conversation}
        self.reads = []

    def table(self, table):
        db = self
        class Query:
            def select(self, *_): return self
            def eq(self, *_): return self
            def maybe_single(self): return self
            def execute(self):
                db.reads.append(table)
                data = db.conversation if table == "conversations" else {"status": "active" if db.active else "inactive"}
                return SimpleNamespace(data=dict(data))
        return Query()


class MockHandlerExecutor(ToolExecutor):
    def __init__(self, *, user_id="u1", org_id="o1", conversation_id="c1", mixin=None, **kwargs):
        kwargs.setdefault("agent_domain", "erp")
        super().__init__(IdentityDB(user_id, org_id), user_id, conversation_id, org_id, **kwargs)
        self.handler = AsyncMock(return_value="ok")
        async def invoke(name, args):
            return await self.handler(name, args)
        self._handlers = {name: (lambda args, name=name: invoke(name, args)) for name in self._handlers}
        if mixin is not None:
            from services.handlers.chat_tool_mixin import ChatToolMixin
            self.task_id = "task1"
            self.tool_confirmer = lambda call, ctx, decision: ChatToolMixin._confirm_tool_call(mixin, call, ctx, decision, "msg1")
