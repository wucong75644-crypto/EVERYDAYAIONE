"""默认图片升级：校验实际请求、积分、参考图选择和旧任务兼容。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.kie_models import calculate_image_cost
from config.smart_model_config import MODEL_TO_GEN_TYPE
from core.config import Settings
from schemas.message import GenerationType
from services.adapters.factory import create_image_adapter
from services.adapters.kie.image_adapter import KieImageAdapter
from services.handlers.ecom_image_handler import _I2I_MODEL
from services.handlers.image_request_settings import resolve_image_generation_settings
from services.media_tool_executor import MediaToolMixin


TEXT_MODEL = "gpt-image-2-5-flare-text-to-image"
EDIT_MODEL = "gpt-image-2-5-flare-image-to-image"
REFERENCE = "https://cdn.example.com/product.png"


@pytest.mark.parametrize("has_reference", [False, True])
@pytest.mark.parametrize("aspect_ratio", ["1:1", "auto", "3:2"])
async def test_default_request_reaches_kie_with_25_model_and_4k(has_reference, aspect_ratio):
    settings = resolve_image_generation_settings(
        {"aspect_ratio": aspect_ratio, "resolution": "4K"}, has_reference,
    )
    expected_model = EDIT_MODEL if has_reference else TEXT_MODEL
    assert settings["model_id"] == expected_model
    assert settings["resolution"] == "4K"
    assert settings["total_credits"] == 16

    client = MagicMock()
    client.create_task = AsyncMock(return_value=SimpleNamespace(task_id="accepted-25"))
    with patch("services.adapters.factory.get_settings", return_value=SimpleNamespace(kie_api_key="test-key")), \
         patch("services.circuit_breaker.is_provider_available", return_value=True), \
         patch("services.adapters.kie.KieClient", return_value=client):
        adapter = create_image_adapter(settings["model_id"] if has_reference else None)
    result = await adapter.generate(
        prompt="A product photo", image_urls=[REFERENCE] if has_reference else None,
        size=settings["aspect_ratio"], resolution=settings["resolution"],
        callback_url="https://app.example.com/callback", wait_for_result=False,
    )
    expected_input = {"prompt": "A product photo", "aspect_ratio": aspect_ratio, "resolution": "4K"}
    if has_reference:
        expected_input["input_urls"] = [REFERENCE]
    request = client.create_task.await_args.args[0].model_dump(mode="json", exclude_none=True)
    assert request == {
        "model": expected_model, "input": expected_input,
        "callBackUrl": "https://app.example.com/callback",
    }
    assert result.task_id == "accepted-25"


@pytest.mark.parametrize("model", [TEXT_MODEL, EDIT_MODEL])
@pytest.mark.parametrize("resolution,credits", [("1K", 6), ("2K", 10), ("4K", 16)])
def test_25_pricing_matches_preflight_and_adapter(model, resolution, credits):
    cost = calculate_image_cost(model, image_count=3, resolution=resolution)
    estimate = KieImageAdapter(MagicMock(), model).estimate_cost(3, resolution)
    assert cost["user_credits"] == estimate.estimated_credits == credits * 3
    assert cost["kie_cost"] == credits * 3


@pytest.mark.parametrize("mode,references,model", [("text_to_image", [], TEXT_MODEL), ("image_to_image", [REFERENCE], EDIT_MODEL)])
async def test_media_tool_uses_single_async_acceptance(mode, references, model):
    tool = MediaToolMixin()
    tool.db = MagicMock()
    args = {"mode":mode,"prompt":"  product  ","image_urls":references,"model":model}
    receipt = {"task_id":"child","status":"submitted","submission_state":"queued"}
    with patch("services.handlers.image_handler.ImageHandler.accept_chat_image",new=AsyncMock(return_value=receipt)) as accept, \
         patch("services.adapters.factory.create_image_adapter") as provider:
        result = await tool._generate_image(args)
    accept.assert_awaited_once_with(tool,args)
    assert result.metadata["accepted"] is True and result.metadata["completed"] is False
    provider.assert_not_called()
    tool.db.rpc.assert_not_called()


@pytest.mark.parametrize("model,refs", [(TEXT_MODEL,None),(EDIT_MODEL,[REFERENCE])])
async def test_transparent_async_payload_uses_one_create_without_retry(model,refs):
    client = MagicMock()
    client.create_task_once = AsyncMock(return_value=SimpleNamespace(is_success=True,task_id="accepted-alpha"))
    client.create_task = AsyncMock()
    result = await KieImageAdapter(client,model).generate(prompt="product",image_urls=refs,
        background="transparent",wait_for_result=False,_chat_image_single_submit=True)
    request=client.create_task_once.await_args.args[0].model_dump(mode="json",exclude_none=True)
    assert request["input"]["background"]=="transparent" and result.task_id=="accepted-alpha"
    client.create_task_once.assert_awaited_once()
    client.create_task.assert_not_called()
    client._schedule_shadow_upload.assert_called_once()


def test_ecom_defaults_and_old_model_routing():
    assert Settings.model_fields["image_agent_kie_model"].default == TEXT_MODEL
    assert Settings.model_fields["image_agent_kie_i2i_model"].default == _I2I_MODEL == EDIT_MODEL
    for model in (TEXT_MODEL, EDIT_MODEL, "gpt-image-2-text-to-image", "gpt-image-2-image-to-image"):
        assert MODEL_TO_GEN_TYPE[model] == GenerationType.IMAGE
        assert KieImageAdapter(MagicMock(), model).model_id == model
    legacy = resolve_image_generation_settings(
        {"model": "gpt-image-2-text-to-image", "aspect_ratio": "1:1", "resolution": "4K"}, True,
    )
    assert legacy["model_id"] == "gpt-image-2-image-to-image"
    assert legacy["resolution"] == "2K"
    assert legacy["total_credits"] == 10


@pytest.mark.parametrize("references", [[], [REFERENCE] * 17])
async def test_25_edit_rejects_missing_or_excessive_references(references):
    client = MagicMock(create_task=AsyncMock())
    with pytest.raises(ValueError):
        await KieImageAdapter(client, EDIT_MODEL).generate(
            prompt="product", image_urls=references, wait_for_result=False,
        )
    client.create_task.assert_not_called()
