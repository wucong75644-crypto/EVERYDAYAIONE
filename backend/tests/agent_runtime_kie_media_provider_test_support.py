from __future__ import annotations

from types import SimpleNamespace

from services.agent.runtime.providers.kie_media import RuntimeKieMediaProvider
from services.agent.runtime.providers.kie_transport import KieHttpResponse


class FakeTransport:
    def __init__(self, *, submit=None, query=None, error: Exception | None = None):
        self.submit_response = submit or KieHttpResponse(
            status_code=200,
            payload={"code": 200, "data": {"taskId": "kie-1"}},
        )
        self.query_response = query or KieHttpResponse(
            status_code=200,
            payload={
                "code": 200,
                "data": {"taskId": "kie-1", "state": "waiting"},
            },
        )
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def submit(self, **kwargs):
        self.calls.append(("submit", kwargs))
        if self.error:
            raise self.error
        return self.submit_response

    async def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        if self.error:
            raise self.error
        return self.query_response


class FakeTaskPort:
    def __init__(self, kind: str = "image"):
        self.kind = kind
        self.calls: list[str] = []

    async def prepare(self, attempt, *, kind):
        self.calls.append("prepare")
        return self._facts(kind)

    async def read(
        self,
        attempt,
        *,
        kind,
        owner_token=None,
        expected_state_version=None,
    ):
        del owner_token, expected_state_version
        self.calls.append("read")
        return self._facts(kind)

    def _facts(self, kind):
        assert kind == self.kind
        provider_request = (
            {
                "model": "sora-2-text-to-video",
                "input": {
                    "prompt": "make it move",
                    "aspect_ratio": "landscape",
                    "n_frames": "10",
                    "remove_watermark": True,
                },
            }
            if kind == "video"
            else {
                "model": "gpt-image-2-image-to-image",
                "input": {
                    "prompt": "make it blue",
                    "input_urls": ["https://cdn.example/reference.png"],
                    "aspect_ratio": "1:1",
                    "resolution": "1K",
                },
            }
        )
        return {
            "kind": kind,
            "source": "model_loop",
            "provider_request": provider_request,
            "provider_request_hash": "b" * 64,
        }


class FakeCredentials:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.hashes: list[str] = []
        self.ownership: list[tuple[object, object]] = []

    async def api_key(
        self,
        attempt,
        *,
        provider_request_hash,
        owner_token=None,
        expected_state_version=None,
    ):
        self.hashes.append(provider_request_hash)
        self.ownership.append((owner_token, expected_state_version))
        if self.error:
            raise self.error
        return "fixture-key"


class FakeProviderFacts:
    def __init__(self):
        self.version = 0
        self.state = "submitted"
        self.provider_task_ref = "kie-1"
        self.cancel_requested = False
        self.calls: list[str] = []

    def _result(self, state, *, provider_task_ref=None):
        self.version += 1
        self.state = state
        if provider_task_ref is not None:
            self.provider_task_ref = provider_task_ref
        return {
            "outcome": state,
            "submission_id": "submission-1",
            "state": state,
            "state_version": self.version,
            "provider_task_ref": provider_task_ref,
        }

    async def create(self, _context):
        self.calls.append("create")
        return {
            "outcome": "created",
            "submission_id": "submission-1",
            "state": "submission_pending",
            "state_version": self.version,
        }

    async def read(self, _context, submission_id):
        self.calls.append("read")
        return {
            "outcome": "readback",
            "submission_id": submission_id,
            "state": self.state,
            "state_version": self.version,
            "provider_task_ref": self.provider_task_ref,
            "cancel_requested_at": (
                "persisted" if self.cancel_requested else None
            ),
        }

    async def submitted(self, **params):
        self.calls.append("submitted")
        return self._result(
            "submitted",
            provider_task_ref=params["provider_task_ref"],
        )

    async def unknown(self, **_params):
        self.calls.append("unknown")
        return self._result("unknown")

    async def rejected(self, **_params):
        self.calls.append("rejected")
        return self._result("failed")

    async def request_cancel(self, **params):
        self.calls.append("request_cancel")
        self.cancel_requested = True
        self.version = params["expected_state_version"]
        return self._result("cancel_requested", provider_task_ref="kie-1")

    async def readback(self, **params):
        self.calls.append("readback")
        self.version = params["expected_state_version"]
        state = {
            "completed": "readback_confirmed",
            "cancelled": "cancelled",
        }.get(params["provider_state"], params["provider_state"])
        return self._result(state, provider_task_ref="kie-1")


class ExistingProviderFacts(FakeProviderFacts):
    async def create(self, _context):
        self.calls.append("create")
        return {
            "outcome": "already_applied",
            "submission_id": "submission-1",
            "state": "submitted",
            "state_version": 8,
            "provider_task_ref": "kie-existing",
        }


def attempt():
    return SimpleNamespace(
        action_id="action-1",
        attempt_id="attempt-1",
        request_hash="a" * 64,
        idempotency_key="runtime-idempotency-1",
        run_id="run-1",
        session_id="session-1",
        scope=SimpleNamespace(
            kind=SimpleNamespace(value="user"),
            scope_id="user-1",
            user_id="user-1",
            org_id="org-1",
        ),
        lease=SimpleNamespace(fencing_token="execution-token"),
    )


def provider(
    transport,
    *,
    credentials=None,
    task_port=None,
    facts=None,
    kind="image",
):
    return RuntimeKieMediaProvider(
        transport,
        task_port=task_port or FakeTaskPort(kind),
        credentials=credentials or FakeCredentials(),
        kind=kind,
        production_ready=True,
        facts=facts or FakeProviderFacts(),
    )


def provider_receipt(*, cancel_unproven=False):
    evidence = {
        "submission_id": "submission-1",
        "state_version": 1,
        "provider_fact_state": "submitted",
        "provider_request_hash": "b" * 64,
        "provider_idempotency_key": "c" * 64,
    }
    if cancel_unproven:
        evidence.update(
            {"cancel_unproven": True, "error_code": "CANCEL_UNPROVEN"},
        )
    return {
        "provider": "kie",
        "provider_task_ref": "kie-1",
        "evidence": evidence,
        "reconciliation_token": "reconcile-token",
        "reconciliation_state_version": 4,
    }
