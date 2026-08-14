from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from services.adapters.types import StreamChunk
from services.agent.runtime.catalog.batch_media_release import (
    build_batch_media_snapshot,
)
from services.agent.runtime.context import ProviderContextPlan
from services.agent.runtime.domain import ModelStepId, StopReason
from services.agent.runtime.infrastructure.model import (
    ExistingProviderModelAdapter,
    compute_request_hash,
    resolve_model_revision,
)
from services.agent.runtime.ports import (
    ModelInputReceipt,
    ModelRequestOptions,
    ModelStepRequest,
)
from services.agent.runtime.production_model import _actions, _messages


MODEL_ID = "qwen3.5-plus"


class _FakeAdapter:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}
        self.closed = False

    async def stream_chat(self, **kwargs: Any):
        self.payload = kwargs
        yield StreamChunk(
            content="已读取参考图", finish_reason="stop",
            prompt_tokens=1, completion_tokens=1,
        )

    async def close(self) -> None:
        self.closed = True


def _image_toolset():
    return build_batch_media_snapshot(
        scope="user", channel="web", gate_state="enabled",
    ).toolset


def _tool_call(index: int, arguments: dict[str, object]):
    return SimpleNamespace(
        index=index, call_id=f"call-{index}", provider_call_id=None,
        name="generate_image",
        arguments_json=json.dumps(arguments, ensure_ascii=False),
    )


def _result(count: int, *, reference_index: int | None = None):
    arguments = lambda index: {
        "prompt": f"variant {index}",
        **({"reference_image_indexes": [reference_index]}
           if reference_index is not None else {}),
    }
    return SimpleNamespace(
        stop_reason=StopReason.TOOL_CALLS,
        tool_calls=tuple(_tool_call(index, arguments(index)) for index in range(count)),
    )


def test_runtime_projects_only_current_input_images_and_keeps_text() -> None:
    projected = _messages(
        [
            {"id": "old", "role": "user", "content": [
                {"type": "text", "text": "历史文字"},
                {"type": "image", "url": "https://cdn/old.png"},
            ]},
            {"id": "current", "role": "user", "content": [
                {"type": "text", "text": "按参考图生成十个方案"},
                {"type": "image", "url": "https://cdn/current-a.png",
                 "asset_id": "asset-a", "workspace_path": "上传/a.png"},
                {"type": "image", "url": "https://cdn/current-b.png",
                 "asset_id": "asset-b", "workspace_path": "上传/b.png"},
            ]},
        ],
        "runtime prompt",
        current_input_message_id="current",
        supports_vision=True,
    )

    assert projected[1] == {"role": "user", "content": "历史文字"}
    assert projected[2]["content"] == [
        {"type": "text", "text": "按参考图生成十个方案"},
        {"type": "text", "text": "[reference_image_index=0]"},
        {"type": "image_url", "image_url": {
            "url": "https://cdn/current-a.png",
        }},
        {"type": "text", "text": "[reference_image_index=1]"},
        {"type": "image_url", "image_url": {
            "url": "https://cdn/current-b.png",
        }},
    ]
    assert "asset-a" not in json.dumps(projected)
    assert "上传/a.png" not in json.dumps(projected, ensure_ascii=False)


def test_runtime_text_projection_regression_and_safe_tool_pair() -> None:
    content = [
        {"type": "text", "text": "先查数据"},
        {"type": "tool_step", "status": "completed", "tool_name": "lookup",
         "tool_call_id": "call-safe", "input": {"id": 1}, "output": "ok"},
    ]
    projected = _messages(
        [{"id": "assistant", "role": "assistant", "content": content}],
        "runtime prompt",
    )
    assert projected[1] == {"role": "assistant", "content": "先查数据"}
    assert projected[2]["tool_calls"][0]["id"] == "call-safe"
    assert projected[3] == {
        "role": "tool", "tool_call_id": "call-safe", "content": "ok",
    }


def test_runtime_fails_closed_when_current_images_need_nonvisual_model() -> None:
    with pytest.raises(RuntimeError, match="RUNTIME_MODEL_VISION_REQUIRED"):
        _messages(
            [{"id": "current", "role": "user", "content": [
                {"type": "text", "text": "分析图片"},
                {"type": "image", "url": "https://cdn/current.png"},
            ]}],
            "runtime prompt",
            current_input_message_id="current",
            supports_vision=False,
        )


@pytest.mark.asyncio
async def test_fake_adapter_receives_multimodal_runtime_projection() -> None:
    messages = _messages(
        [{"id": "current", "role": "user", "content": [
            {"type": "text", "text": "分析图片"},
            {"type": "image", "url": "https://cdn/current.png"},
        ]}],
        "runtime prompt", current_input_message_id="current",
    )
    plan = ProviderContextPlan.build(
        messages=messages, tools=[], context_epoch_id="epoch-1",
        model_step=1, stable_prefix_blocks=0,
    )
    options = ModelRequestOptions()
    revision = resolve_model_revision(MODEL_ID)
    request_hash = compute_request_hash(
        model_id=MODEL_ID, model_revision=revision,
        prompt_revision="batch-media-v1", tool_catalog_revision="catalog-v7",
        input_receipt_hash="receipt", context_plan_hash=plan.plan_hash,
        options=options,
    )
    request = ModelStepRequest(
        model_step_id=ModelStepId("step-1"), model_id=MODEL_ID,
        request_hash=request_hash,
        input_receipt=ModelInputReceipt(
            receipt_id="receipt-1", receipt_hash="receipt",
            context_plan_hash=plan.plan_hash,
        ),
        context_plan=plan, model_revision=revision,
        prompt_revision="batch-media-v1", tool_catalog_revision="catalog-v7",
        options=options,
    )
    adapter = _FakeAdapter()
    result = await ExistingProviderModelAdapter(
        adapter_factory=lambda *_args, **_kwargs: adapter,
    ).complete(request)

    assert result.output and result.output.content == "已读取参考图"
    assert adapter.payload["messages"] == messages
    assert adapter.closed is True


def test_generate_image_schema_rejects_urls_and_invalid_reference_indexes() -> None:
    toolset = _image_toolset()
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        toolset.validate_call("generate_image", {
            "prompt": "variant", "image_url": "https://evil.example/a.png",
        })
    with pytest.raises(ValueError, match="REFERENCE_IMAGE_INDEX_INVALID"):
        _actions(
            _result(1, reference_index=1), "run-1", toolset,
            reference_image_count=1,
        )
    with pytest.raises(ValueError, match="TOOL_CALL_SCHEMA_INVALID"):
        _actions(
            SimpleNamespace(
                stop_reason=StopReason.TOOL_CALLS,
                tool_calls=(_tool_call(0, {
                    "prompt": "variant", "reference_image_indexes": [-1],
                }),),
            ),
            "run-1", toolset, reference_image_count=1,
        )


def test_ten_generate_image_calls_are_allowed_and_eleventh_fails_closed() -> None:
    toolset = _image_toolset()
    _, actions = _actions(_result(10), "run-10", toolset)
    assert len(actions) == 10
    assert [action["index"] for action in actions] == list(range(10))
    with pytest.raises(ValueError, match="IMAGE_ACTION_BATCH_LIMIT_EXCEEDED"):
        _actions(_result(11), "run-11", toolset)
