"""Owner controls for existing frozen images; no prompt preparation or provider IO."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from core.config import get_settings
from core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from services.handlers.chat_image_request import (
    REQUEST_KEY, LIFECYCLE_KEY, ChatImageInputResolver, canonical_hash,
    validate_single_image_request, verify_frozen_request,
    chat_image_acceptance_allowed,
)


class ChatImageControls:
    def __init__(self, db, user_id, org_id):
        self.db, self.user_id, self.org_id = db, user_id, org_id

    async def task(self, task_id):
        row = await asyncio.to_thread(lambda: self.db.table("tasks").select("*")
            .eq("id", task_id).eq("user_id", self.user_id).single().execute().data)
        if (not isinstance(row, dict) or row.get("org_id") != self.org_id
                or row.get("type") != "image" or REQUEST_KEY not in (row.get("request_params") or {})
                or row["request_params"][REQUEST_KEY]["origin"].get("destination") == "skill_trial"):
            raise NotFoundError("聊天图片任务", task_id)
        return row

    async def details(self, task_id):
        row = await self.task(task_id)
        params = row["request_params"]
        snapshot = params[REQUEST_KEY]
        verify_frozen_request(snapshot)
        phase = params[LIFECYCLE_KEY]["phase"]
        previews=[]
        if snapshot["references"]:
            origin=snapshot["origin"]
            owner=SimpleNamespace(db=self.db,user_id=self.user_id,workspace_user_id=origin["workspace_owner_id"],
                org_id=self.org_id,context_scope=origin["context_scope"],conversation_id=row["conversation_id"],resource_manifest=None)
            resolver=ChatImageInputResolver(owner,base_revision=origin["base_context_revision"],input_message_id=origin["input_message_id"])
            for index,reference in enumerate(snapshot["references"]):
                try: url=await asyncio.to_thread(resolver.preview,reference)
                except (ValueError,PermissionError,OSError): url=None
                previews.append({"index":index,"url":url,"available":bool(url)})
        from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
        transparent_supported = (get_settings().chat_image_transparent_enabled and
            "transparent" in IMAGE_MODEL_CONFIGS.get(snapshot["model"], {}).get("supported_backgrounds", ()))
        return {"task_id": row["id"], "message_id": row["assistant_message_id"],
            "status": row["status"], "submission_state": phase,
            "input": deepcopy(snapshot), "reference_previews":previews, "result": [row["result_data"]] if row.get("result_data") else [],
            "credits_used": row.get("credits_used") or 0,
            "platform_cost": params.get("_media_platform_cost_v1"),
            "feedback": params.get("_media_feedback_v1"),
            "can_stop": phase == "queued",
            "can_replay": phase == "published" and chat_image_acceptance_allowed(get_settings(), self.user_id),
            "cancel_explanation": "排队时可停止；任务一经领取或提交便无法撤回，将继续核实与结算",
            "capabilities": {"reference_weights": False, "transparent_background": transparent_supported,
                "mask_edit": False, "quality_check": False}}

    async def replay(self, task_id, request_id):
        row = await self.task(task_id)
        original = row["request_params"][REQUEST_KEY]
        verify_frozen_request(original)
        if row["request_params"][LIFECYCLE_KEY]["phase"] != "published":
            raise ValidationError("原任务尚未完成核实与结算，请等待结果")
        origin = original["origin"]
        snapshot = deepcopy(original)
        snapshot.pop("request_hash")
        snapshot["origin"].pop("tool_call_id", None)
        snapshot["origin"].update(retry_of_task_id=task_id, retry_request_id=request_id)
        snapshot["source_task_id"] = task_id
        snapshot["request_hash"] = canonical_hash(snapshot)
        receipt = await self._replay_rpc(task_id, request_id, snapshot, allow_new=False)
        if receipt.get("outcome") == "replay":
            return self._receipt(row, original, receipt)
        if not chat_image_acceptance_allowed(get_settings(), self.user_id):
            raise PermissionDeniedError("新的图片请求尚未开放")
        if original.get("background")=="transparent" and not get_settings().chat_image_transparent_enabled:
            raise PermissionDeniedError("透明背景接受尚未开放")
        owner = SimpleNamespace(db=self.db, user_id=self.user_id, workspace_user_id=self.user_id,
            org_id=self.org_id, conversation_id=row["conversation_id"], context_scope="user",
            execution_mode="interactive", resource_manifest=None)
        resolver = ChatImageInputResolver(owner, base_revision=origin["base_context_revision"],
            input_message_id=origin["input_message_id"])
        try:
            await asyncio.to_thread(resolver.verify, original["references"])
            args={key:original[key] for key in ("mode", "prompt", "model", "aspect_ratio", "resolution", "output_format")}
            if "background" in original: args["background"]=original["background"]
            current = validate_single_image_request(args, len(original["references"]))
            # A retry must not silently adopt a new charge. A changed price
            # requires a new explicit request with the current preview.
            if current["estimated_credits"] != original["estimated_credits"]:
                raise ValueError("模型价格已变化，请查看成本预览后重新发起生成")
        except (ValueError, PermissionError, FileNotFoundError) as error:
            raise ValidationError(str(error)) from error
        snapshot.pop("request_hash")
        snapshot["estimated_provider_credits"] = current["estimated_provider_credits"]
        snapshot["budget"] = {"max_requests": get_settings().chat_image_max_requests,
            "max_credits": get_settings().chat_image_max_credits}
        snapshot["request_hash"] = canonical_hash(snapshot)
        result = await self._replay_rpc(task_id, request_id, snapshot, allow_new=True)
        if not isinstance(result, dict) or result.get("outcome") not in {"accepted", "replay"}:
            raise RuntimeError("CHAT_IMAGE_ACCEPTANCE_NOT_CONFIRMED")
        return self._receipt(row, snapshot, result)

    async def _replay_rpc(self, task_id, request_id, snapshot, *, allow_new):
        return await asyncio.to_thread(lambda: self.db.rpc("replay_chat_image_snapshot", {
            "p_source_task_id": task_id, "p_request_id": request_id,
            "p_snapshot": snapshot, "p_org_id": self.org_id, "p_allow_new": allow_new,
        }).execute().data)

    @staticmethod
    def _receipt(row, snapshot, result):
        return {"status": "submitted", **result, "conversation_id": row["conversation_id"],
            "estimated_credits": snapshot["estimated_credits"]}

    async def stop(self, task_id):
        await self.task(task_id)
        return await asyncio.to_thread(lambda: self.db.rpc("stop_queued_chat_image", {
            "p_task_id": task_id, "p_org_id": self.org_id,
        }).execute().data)

    async def feedback(self, task_id, rating):
        await self.task(task_id)
        return await asyncio.to_thread(lambda: self.db.rpc("feedback_chat_image", {
            "p_task_id": task_id, "p_rating": rating, "p_org_id": self.org_id,
        }).execute().data)
