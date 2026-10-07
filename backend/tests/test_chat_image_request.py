"""Strict image contracts: no database/provider calls."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest
from PIL import Image

from services.handlers.chat_image_request import (
    ChatImageInputResolver, freeze_image_request, validate_single_image_request,
    verify_frozen_request,
)
from services.handlers.image_request_settings import resolve_image_generation_settings


def test_explicit_mode_does_not_follow_analysis_attachments():
    assert validate_single_image_request({"mode":"text_to_image", "prompt":"  原文  "}, 0)["prompt"] == "  原文  "
    for args, count in [({"prompt":"p"},0), ({"mode":"text_to_image","prompt":"p"},1),
                        ({"mode":"image_to_image","prompt":"p"},0)]:
        with pytest.raises(ValueError):
            validate_single_image_request(args, count)


@pytest.mark.parametrize("extra", [
    {"num_images":2}, {"prompts":["p"]}, {"resolution":"1024"},
    {"output_format":"jpeg"}, {"aspect_ratio":"5:4"}, {"mask":"x"},
    {"reference_weight":0.5}, {"transparent_background":True},
    {"_task_slot_id":"parent"}, {"org_id":"fake"},
])
def test_no_ignored_or_unimplemented_parameters(extra):
    with pytest.raises(ValueError):
        validate_single_image_request({"mode":"text_to_image","prompt":"p",**extra},0)


def test_model_pair_and_reference_limits():
    assert validate_single_image_request({"mode":"image_to_image","prompt":"p"},2)["model"].endswith("image-to-image")
    with pytest.raises(ValueError):
        validate_single_image_request({"mode":"image_to_image","prompt":"p", "model":"gpt-image-2-5-flare-text-to-image"},1)
    with pytest.raises(ValueError):
        validate_single_image_request({"mode":"image_to_image","prompt":"p"},17)


def test_snapshot_freezes_all_inputs_and_preserves_order():
    args = {"mode":"image_to_image", "prompt":"最终原文", "variant_id":"v1"}
    refs = [{"workspace_path":"B.png","content_sha256":"b"*64,"role":"产品"},
            {"workspace_path":"A.png","content_sha256":"a"*64,"role":"风格"}]
    origin = {"parent_task_id":"parent", "tool_call_id":"call"}
    snapshot = freeze_image_request(args, refs, origin=origin, max_requests=4, max_credits=100)
    verify_frozen_request(snapshot)
    refs[0]["workspace_path"] = "C.png"
    args["prompt"] = "后来改动"
    origin["tool_call_id"] = "later"
    assert snapshot["prompt"] == "最终原文"
    assert [r["workspace_path"] for r in snapshot["references"]] == ["B.png","A.png"]
    modified = deepcopy(snapshot)
    modified["references"].reverse()
    with pytest.raises(ValueError, match="CHANGED"):
        verify_frozen_request(modified)


def test_native_batch_settings_keep_existing_normalization():
    settings = resolve_image_generation_settings({"num_images":4,"resolution":"1024x1024"},True)
    assert settings["num_images"] == 4
    assert settings["resolution"] == "1K"
    assert settings["model_id"].endswith("image-to-image")
    strict=validate_single_image_request({"mode":"text_to_image","prompt":"p",
        "model":"gpt-image-2-text-to-image","resolution":"4K","aspect_ratio":"1:1"},0)
    assert strict["resolution"] == "4K"


@pytest.fixture
def resolver(tmp_path):
    from services.file_executor import FileExecutor
    from services.tools.resource_access import ResourceAccessBoundary, ResourceRule
    files = FileExecutor(str(tmp_path), "00000000-0000-4000-8000-000000000001", None)
    owner = SimpleNamespace(user_id="00000000-0000-4000-8000-000000000001",
        workspace_user_id="00000000-0000-4000-8000-000000000001", org_id=None,
        context_scope="user", conversation_id="conv", resource_manifest=None,
        resource_access_boundary=ResourceAccessBoundary((ResourceRule(("read",),directories=(".",)),),"test",True))
    Image.new("RGB",(3,3)).save(files.resolve_safe_path("A.png"))
    Image.new("RGB",(3,3),"red").save(files.resolve_safe_path("B.png"))
    return ChatImageInputResolver(owner,base_revision=2,input_message_id="input",files=files)


def test_signed_reference_original_bytes_and_replacement(resolver):
    from services.file_resources import file_version
    path = resolver.files.files.resolve_safe_path("B.png")
    ref = resolver.files.codec.issue("B.png",file_version(path))
    result = resolver.resolve([{"resource_ref":ref,"role":"主体"}])
    assert result[0]["workspace_path"] == "B.png"
    assert result[0]["content_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    resolver.verify(result)
    Image.new("RGB",(4,4)).save(path)
    with pytest.raises(ValueError):
        resolver.verify(result)


def test_no_url_path_or_cross_workspace_forgery(resolver):
    for value in ("https://example.test/a.png","../B.png","B.png"):
        with pytest.raises(ValueError):
            resolver.resolve([{"resource_ref":value,"role":"主体"}])
    from services.file_resources import FileReferenceCodec, file_version
    foreign = FileReferenceCodec(org_id=None,owner_id="foreign").issue("A.png",file_version(resolver.files.files.resolve_safe_path("A.png")))
    with pytest.raises(PermissionError):
        resolver.resolve([{"resource_ref":foreign,"role":"风格"}])


def test_deleted_exact_asset_path_cannot_select_other_basename(resolver):
    from unittest.mock import MagicMock
    root=resolver.files.root
    (root/"other").mkdir()
    Image.new("RGB",(3,3)).save(root/"other/A.png")
    (root/"A.png").unlink()
    db=MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data={
        "id":"asset", "org_id":None,"storage_owner_key":resolver.owner.workspace_user_id,
        "storage_scope":"user","status":"ready","media_type":"image","workspace_path":"A.png"}
    resolver.owner.db=db
    with pytest.raises(ValueError,match="RESOURCE_NOT_FOUND"):
        resolver.resolve([{"asset_id":"asset","role":"主体"}])


def test_async_schema_single_output_and_exact_source():
    from services.tools.definitions.media import _schema_generate_image_async
    from services.agent.tool_args_validator import validate_tool_args
    schema=_schema_generate_image_async()["function"]
    assert schema["name"] == "generate_image"
    assert schema["parameters"]["oneOf"] == [
        {"required": ["mode", "prompt"]}, {"required": ["plan_source"]},
    ]
    plan_source = schema["parameters"]["properties"]["plan_source"]
    assert plan_source["required"] == ["plan_id", "revision", "item_id"]
    assert plan_source["additionalProperties"] is False
    selected = [_schema_generate_image_async()]
    planned_args = {"plan_source": {
        "plan_id": "00000000-0000-4000-8000-000000000001",
        "revision": 1,
        "item_id": "00000000-0000-4000-8000-000000000002",
    }}
    cleaned, error = validate_tool_args("generate_image", planned_args, selected)
    assert error is None
    assert cleaned == planned_args
    _, error = validate_tool_args("generate_image", {
        **planned_args, "mode": "text_to_image", "prompt": "cannot override saved plan",
    }, selected)
    assert error is not None
    assert "num_images" not in schema["parameters"]["properties"]
    assert "prompts" not in schema["parameters"]["properties"]
    assert "model" not in schema["parameters"]["properties"]
    assert "model_name" not in schema["parameters"]["properties"]
    assert schema["parameters"]["additionalProperties"] is False


def test_tool_advertises_only_default_pair_without_changing_native_models():
    from services.handlers.chat_image_request import default_chat_image_model, image_capabilities
    advertised = image_capabilities()
    assert {model["model"] for model in advertised} == {
        default_chat_image_model("text_to_image"), default_chat_image_model("image_to_image"),
    }
    assert not any(model["model"] == "google/nano-banana" for model in advertised)
    assert resolve_image_generation_settings({"model":"google/nano-banana"},False)["model_id"] == "google/nano-banana"


@pytest.mark.parametrize("args,code", [
    ({"mode":"text_to_image","prompt":"p","model":"gpt-image-2-5-flare-text-to-image"},"IMAGE_MODEL_SELECTION_DISABLED"),
    ({"prompt":"p","model_name":"google/nano-banana","size":"1K","format":"PNG"},"IMAGE_MODEL_SELECTION_DISABLED"),
    ({"mode":"text_to_image","prompt":"p","size":"1K"},"IMAGE_REQUEST_FIELDS_INVALID"),
])
def test_new_tool_input_cannot_select_models_or_invent_aliases(args,code):
    from services.handlers.chat_image_request import ChatImageNotAcceptedError, validate_chat_image_tool_fields
    with pytest.raises(ChatImageNotAcceptedError,match=code):
        validate_chat_image_tool_fields(args)


def test_frozen_model_remains_valid_when_platform_default_changes(monkeypatch):
    from services.handlers import chat_image_request
    monkeypatch.setattr(chat_image_request,"DEFAULT_IMAGE_MODEL_ID","google/nano-banana")
    snapshot = freeze_image_request({"mode":"text_to_image","prompt":"original"},[],
        origin={},max_requests=2,max_credits=24)
    monkeypatch.setattr(chat_image_request,"DEFAULT_IMAGE_MODEL_ID","gpt-image-2-5-flare-text-to-image")
    verify_frozen_request(snapshot)
    replay_args = {key:snapshot[key] for key in ("mode","prompt","model","aspect_ratio","resolution","output_format")}
    validated = validate_single_image_request(replay_args,0)
    assert validated["model"] == "google/nano-banana" and validated["estimated_credits"] == snapshot["estimated_credits"]


def test_legacy_urls_must_be_selected_current_originals(resolver):
    from services.handlers.resource_manifest import ResourceAsset, ResourceManifest
    asset=ResourceAsset("id","B.png","B.png","image/png",None,"https://trusted.test/B.png")
    resolver.owner.resource_manifest=ResourceManifest("task","input",(asset,),"input_message")
    args=resolver.normalize_legacy({"prompt":"p","image_urls":[asset.url]})
    assert args["mode"] == "image_to_image"
    assert resolver.resolve(args["references"])[0]["workspace_path"] == "B.png"
    assert resolver.normalize_legacy({"prompt":"p"})["mode"] == "text_to_image"
    with pytest.raises(PermissionError):
        resolver.normalize_legacy({"prompt":"p","image_urls":["https://untrusted.test/B.png"]})


@pytest.mark.asyncio
async def test_acceptance_disabled_before_any_database_or_paid_io():
    from unittest.mock import MagicMock
    from services.handlers.image_handler import ImageHandler
    db=MagicMock()
    with pytest.raises(PermissionError,match="DISABLED"):
        await ImageHandler(db).accept_chat_image(None,{"mode":"text_to_image","prompt":"p"})
    db.table.assert_not_called()
    db.rpc.assert_not_called()


@pytest.mark.parametrize("image_mode,alpha,expected", [("RGB",255,False),("RGBA",255,False),("RGBA",0,True),("P",0,True)])
def test_transparency_checks_actual_pixels_not_png_extension(resolver,image_mode,alpha,expected):
    from services.handlers.chat_image_lifecycle import inspect_transparent_result
    path=resolver.files.files.resolve_safe_path("result.png")
    if image_mode=="P":
        image=Image.new("P",(4,4),0)
        image.save(path,transparency=0)
    else:
        Image.new(image_mode,(4,4),(10,20,30,alpha) if image_mode=="RGBA" else (10,20,30)).save(path)
    result=inspect_transparent_result(resolver.owner,"result.png",files=resolver.files.files)
    assert result["has_transparency"] is expected
    assert result["quality_checks"]["file_integrity"] is True


@pytest.mark.parametrize("contents", [b"not a png",None])
def test_transparency_invalid_output_is_contract_failure(resolver,contents):
    from services.handlers.chat_image_lifecycle import inspect_transparent_result
    path=resolver.files.files.resolve_safe_path("result.png")
    if contents is None: Image.new("RGB",(3,3)).save(path,format="JPEG")
    else: path.write_bytes(contents)
    with pytest.raises(ValueError,match="OUTPUT_INVALID"):
        inspect_transparent_result(resolver.owner,"result.png",files=resolver.files.files)


def test_background_only_supported_models_and_png():
    supported=validate_single_image_request({"mode":"text_to_image","prompt":"p","background":"transparent"},0)
    assert supported["background"]=="transparent"
    for extra in [{"output_format":"jpg"},{"model":"nano-banana-pro"}]:
        with pytest.raises(ValueError,match="(?:BACKGROUND|OUTPUT_FORMAT)_UNSUPPORTED"):
            validate_single_image_request({"mode":"text_to_image","prompt":"p","background":"transparent",**extra},0)


@pytest.mark.asyncio
async def test_cost_preview_uses_current_price_and_closed_transparency_gate(monkeypatch):
    from api.routes.task import ImageEstimateRequest,ReplayImageRequest,estimate_image
    from core.config import Settings
    from pydantic import ValidationError
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",chat_image_async_enabled=True)
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    ctx=SimpleNamespace(user_id="00000000-0000-4000-8000-000000000001")
    preview=await estimate_image(ImageEstimateRequest(mode="text_to_image",resolution="4K",image_count=3),ctx)
    assert preview["total_credits"]==48 and preview["within_budget"] is True
    alpha=await estimate_image(ImageEstimateRequest(mode="text_to_image",background="transparent"),ctx)
    assert alpha["acceptance_enabled"] is False
    with pytest.raises(ValidationError): ReplayImageRequest(request_id="00000000-0000-4000-8000-000000000001",prompt="forge")


def test_same_prompt_stable_variants_do_not_disable_repeat_guard():
    import json
    from services.handlers.chat.execution_engine import _RepeatedToolCallGuard
    guard=_RepeatedToolCallGuard()
    def call(variant): return [{"id":"call","name":"generate_image","arguments":json.dumps({"mode":"text_to_image","prompt":"same","variant_id":variant})}]
    assert [guard.observe(call(v)) for v in ("v1","v2","v3")]==["continue"]*3
    assert guard.observe(call("v3"))=="nudge" and guard.observe(call("v3"))=="stop"


def test_reference_preview_reauthorizes_and_never_shows_replaced_original(resolver,monkeypatch):
    from services.file_resources import file_version
    from unittest.mock import Mock
    path=resolver.files.files.resolve_safe_path("B.png")
    identity=resolver.files.codec.issue("B.png",file_version(path))
    frozen=resolver.resolve([{"resource_ref":identity,"role":"product"}])[0]
    url=Mock(return_value="signed-current-url");monkeypatch.setattr(resolver.files.files,"get_cdn_url",url)
    assert resolver.preview(frozen)=="signed-current-url"
    Image.new("RGB",(7,7)).save(path)
    with pytest.raises(ValueError): resolver.preview(frozen)
    assert url.call_count==1


def test_acceptance_rollout_accounts_are_validated_and_fail_closed():
    from core.config import Settings
    from pydantic import ValidationError
    from services.handlers.chat_image_request import chat_image_acceptance_allowed
    actor="00000000-0000-4000-8000-000000000001"
    other="00000000-0000-4000-8000-000000000002"
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",
        chat_image_async_enabled=True,chat_image_allowed_user_ids=f" {actor}, {actor} ")
    assert settings.chat_image_allowed_user_ids==actor
    assert chat_image_acceptance_allowed(settings,actor) is True
    assert chat_image_acceptance_allowed(settings,other) is False
    assert chat_image_acceptance_allowed(settings,None) is False
    settings.chat_image_async_enabled=False
    assert chat_image_acceptance_allowed(settings,actor) is False
    with pytest.raises(ValidationError):
        Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",
            chat_image_allowed_user_ids="invalid")


@pytest.mark.asyncio
async def test_rollout_gate_blocks_tool_and_trial_before_database_io(monkeypatch):
    from core.config import Settings
    from services.handlers.image_handler import ImageHandler
    from unittest.mock import Mock
    actor="00000000-0000-4000-8000-000000000001"
    other="00000000-0000-4000-8000-000000000002"
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",
        chat_image_async_enabled=True,chat_image_allowed_user_ids=actor)
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    database=Mock()
    handler=ImageHandler(database)
    with pytest.raises(PermissionError,match="ASYNC_DISABLED"):
        await handler.accept_chat_image(SimpleNamespace(user_id=other),{"mode":"text_to_image","prompt":"p"})
    with pytest.raises(PermissionError,match="ASYNC_DISABLED"):
        await handler.accept_image_trial(actor_id=other,org_id=None,trial_id="trial",conversation_id="conv",
            args={},trial_facts={},result={})
    database.table.assert_not_called()
    database.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_rollout_cost_preview_reports_actor_specific_acceptance(monkeypatch):
    from core.config import Settings
    from api.routes.task import ImageEstimateRequest,estimate_image
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",
        chat_image_async_enabled=True,chat_image_allowed_user_ids="00000000-0000-4000-8000-000000000001")
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    body=ImageEstimateRequest(mode="text_to_image")
    owner=await estimate_image(body,SimpleNamespace(user_id=settings.chat_image_allowed_user_ids))
    other=await estimate_image(body,SimpleNamespace(user_id="00000000-0000-4000-8000-000000000002"))
    assert owner["acceptance_enabled"] is True and other["acceptance_enabled"] is False
    assert owner["per_image_credits"]==other["per_image_credits"]
