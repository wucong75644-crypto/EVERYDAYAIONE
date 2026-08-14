from __future__ import annotations

from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt,
    ProviderState,
)


class FakeSpecialistProvider:
    async def submit(self, attempt, request, *, idempotency_key):
        return ProviderReceipt(
            state=ProviderState.COMPLETED,
            provider="fake",
            request_hash=attempt.request_hash,
            result={"summary": "ok", "count": 1},
        )

    async def reconcile(self, attempt, receipt):
        return ProviderReceipt(
            state=ProviderState.COMPLETED,
            provider="fake",
            request_hash=attempt.request_hash,
            result={"summary": "reconciled"},
        )

    async def cancel(self, attempt, receipt):
        return ProviderReceipt(
            state=ProviderState.CANCELLED,
            provider="fake",
            request_hash=attempt.request_hash,
            evidence={"cancelled": True},
        )


class UnknownSpecialistProvider(FakeSpecialistProvider):
    async def submit(self, attempt, request, *, idempotency_key):
        raise TimeoutError("response lost")


class AcceptedSpecialistProvider(FakeSpecialistProvider):
    async def submit(self, attempt, request, *, idempotency_key):
        return ProviderReceipt(
            state=ProviderState.ACCEPTED,
            provider="fake",
            request_hash=attempt.request_hash,
            provider_task_ref="task-1",
            evidence={"accepted": True},
        )


class FakeErpDispatcher:
    async def execute(self, tool_name, action, params):
        from services.kuaimai.registry import TOOL_REGISTRIES

        assert action in TOOL_REGISTRIES[tool_name]
        return type(
            "Result",
            (),
            {
                "status": "success",
                "summary": f"{tool_name}:{action}",
                "data": [],
            },
        )()


class FakeCallbackRepository:
    def __init__(self):
        self.events = []

    async def callback(self, **kwargs):
        self.events.append(kwargs)
        return {"outcome": "accepted"}


class FakeSpecialistFacts:
    def __init__(self):
        self.calls = []

    async def cost(self, operation, item, **extra):
        self.calls.append(("cost", operation))
        return {"outcome": "applied"}

    async def provider_terminal(self, **params):
        self.calls.append(("provider", params["state"]))
        return {"outcome": params["state"]}

    async def provider_reconcile(self, **params):
        self.calls.append(("reconcile", params["resolution"]))
        return {"outcome": params["resolution"]}


class FakeRpcResponse:
    def __init__(self, data):
        self.data = data

    async def execute(self):
        return self


class FakeRpcDatabase:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return FakeRpcResponse(self.data)

    async def provider_unknown(self, **params):
        self.calls.append(("provider", "unknown"))
        return {"outcome": "unknown"}


class FakeObjectStore:
    def __init__(self):
        self.items = {}

    async def put_verified(self, key, content, *, content_hash):
        import hashlib

        actual = hashlib.sha256(content).hexdigest()
        self.items[key] = content
        return {"verified": actual == content_hash, "content_hash": actual}

    async def get(self, key):
        return self.items[key]


async def resolved_value(value):
    return value
