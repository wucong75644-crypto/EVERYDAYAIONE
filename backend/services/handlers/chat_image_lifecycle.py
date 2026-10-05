"""Bounded submission and settlement of existing image tasks, no new runtime.

The database owns leases/ledger/publication. Provider creation is single-shot;
storage/asset/message retries only replay the recorded provider result.
"""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from loguru import logger

from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from services.handlers.chat_image_request import (
    REQUEST_KEY, LIFECYCLE_KEY, ChatImageInputResolver, validate_single_image_request,
    verify_frozen_request,
)


def is_chat_image(task):
    return task.get("type") == "image" and REQUEST_KEY in (task.get("request_params") or {})


def due(value):
    if not value:
        return False
    return datetime.fromisoformat(str(value).replace("Z","+00:00")) <= datetime.now(timezone.utc)


def inspect_transparent_result(owner, workspace_path, *, files=None):
    """Inspect saved bytes with existing Pillow, never infer alpha from PNG."""
    from PIL import Image
    from services.file_resources import FileTargetResolver
    path=FileTargetResolver(owner,files).guarded(workspace_path)
    if path.stat().st_size>64*1024*1024:
        raise ValueError("IMAGE_TRANSPARENCY_OUTPUT_INVALID")
    data=path.read_bytes()  # Filesystem errors remain recoverable storage errors.
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format!="PNG" or image.width*image.height>32_000_000:
                raise ValueError("IMAGE_TRANSPARENCY_OUTPUT_INVALID")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            alpha=image.convert("RGBA").getchannel("A").getextrema()
            return {"width":image.width,"height":image.height,"has_transparency":alpha[0]<255,
                "quality_checks":{"file_integrity":True,"alpha_min":alpha[0],"alpha_max":alpha[1]}}
    except (OSError,SyntaxError,ValueError,Image.DecompressionBombError) as error:
        raise ValueError("IMAGE_TRANSPARENCY_OUTPUT_INVALID") from error


class ChatImageLifecycle:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self._cursors = {}

    def scoped(self, task):
        return ScopedDatabaseClient(self.db, DatabaseScope(task["user_id"], task.get("org_id"), DatabaseAccessKind.WORKER))

    async def rpc(self, task, name, **params):
        return await asyncio.to_thread(lambda:self.scoped(task).rpc(name,{
            **params,"p_task_id":task["id"],"p_org_id":task.get("org_id"),
        }).execute().data)

    async def refresh(self, task):
        return await asyncio.to_thread(lambda:self.scoped(task).table("tasks").select("*").eq("id",task["id"]).single().execute().data)

    async def scan(self, limit=20):
        """Indexed, paginated short work. Separate from supplier query jitter."""
        # Include inactive tenants: previously accepted work still settles.
        organizations=await asyncio.to_thread(lambda:self.db.table("organizations").select("id").execute().data)
        for org in [None,*[str(row["id"]) for row in organizations or []]]:
            cursor=self._cursors.get(org)
            scoped=ScopedDatabaseClient(self.db,DatabaseScope(None,org,DatabaseAccessKind.WORKER))
            tasks=await asyncio.to_thread(lambda:scoped.rpc("scan_chat_image_work",{
                "p_org_id":org,"p_after_created":cursor[0] if cursor else None,
                "p_after_id":cursor[1] if cursor else None,"p_limit":limit,
            }).execute().data)
            self._cursors[org]=(tasks[-1]["created_at"],tasks[-1]["id"]) if tasks else None
            for task in tasks or []:
                try:
                    await self.advance(await self.refresh(task))
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("chat_image_recovery_failed | task={} | error_type={}",task["id"],type(error).__name__)

    async def advance(self, task):
        state=task["request_params"][LIFECYCLE_KEY]
        phase=state["phase"]
        if phase == "queued":
            created=datetime.fromisoformat(task["created_at"].replace("Z","+00:00"))
            if (datetime.now(timezone.utc)-created).total_seconds() > self.settings.chat_image_queue_timeout_seconds:
                await self.definite_failure(task,"排队超时，未提交供应商")
            else:
                await self.submit(task)
        elif phase == "submitting" and due(state.get("lease_expires_at")):
            if state.get("dispatch_started_at"):
                await self.rpc(task,"mark_chat_image_uncertain",p_claim_token=state["claim_token"],
                    p_timeout_seconds=self.settings.chat_image_uncertain_timeout_seconds)
            else:
                # No send marker => no provider IO is permitted. Refund and
                # publish the interruption; retry creates a new local version.
                await self.definite_failure(task,"提交前服务中断，未发送供应商请求")
        elif phase == "uncertain" and due(state.get("uncertain_deadline")):
            await self.publish(task,[{"type":"image","url":None,"failed":True,
                "error":"无法确认供应商受理，积分已退还；可能发生的生成费用由平台承担"}],
                "failed","供应商受理未确认，平台承担",platform_pays=True)
        elif phase == "accepted":
            from core.task_config import IMAGE_TASK_TIMEOUT_MINUTES
            accepted=datetime.fromisoformat(state["accepted_at"].replace("Z","+00:00"))
            if (datetime.now(timezone.utc)-accepted).total_seconds() > IMAGE_TASK_TIMEOUT_MINUTES*60:
                await self.rpc(task,"mark_chat_image_uncertain",p_claim_token=state["claim_token"],
                    p_timeout_seconds=self.settings.chat_image_uncertain_timeout_seconds)
        elif phase == "settling":
            await self.finish_with_lock(task)
        elif phase == "published" and state.get("delivery_pending"):
            await self.deliver(task)

    async def submit(self, task):
        from services.adapters.factory import create_image_adapter
        from services.adapters.kie.image_adapter import KieSubmissionUncertainError
        from services.handlers.image_handler import ImageHandler
        from api.deps import get_task_limit_service
        from core.exceptions import TaskQueueFullError
        snapshot=task["request_params"][REQUEST_KEY]
        try:
            verify_frozen_request(snapshot)
            origin=snapshot["origin"]
            owner=SimpleNamespace(db=self.scoped(task), user_id=task["user_id"],
                workspace_user_id=origin["workspace_owner_id"],org_id=task.get("org_id"),
                context_scope=origin["context_scope"],conversation_id=task["conversation_id"],
                resource_manifest=None,execution_mode="interactive")
            resolver=ChatImageInputResolver(owner,base_revision=origin["base_context_revision"],input_message_id=origin["input_message_id"])
            await asyncio.to_thread(resolver.verify,snapshot["references"])
            fields={key:snapshot[key] for key in ("mode","prompt","model","aspect_ratio","resolution","output_format")}
            if "background" in snapshot: fields["background"]=snapshot["background"]
            current=validate_single_image_request(fields,len(snapshot["references"]))
            if current["estimated_credits"] != snapshot["estimated_credits"]:
                raise ValueError("模型价格已变化，请重新确认")
            urls=[resolver.files.files.get_cdn_url(ref["workspace_path"]) for ref in snapshot["references"]]
            if any(not url for url in urls):
                raise ValueError("参考原图地址不可用，请重新选择")
            from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
            maximum=IMAGE_MODEL_CONFIGS[snapshot["model"]].get("max_image_size_mb",30)*1024*1024
            if any(ref["size"]>maximum for ref in snapshot["references"]):
                raise ValueError("参考原图超过模型允许大小")
        except (ValueError,PermissionError,FileNotFoundError) as error:
            await self.definite_failure(task,str(error))
            return
        limiter=await get_task_limit_service()
        try:
            await limiter.acquire_image_slot(task["user_id"],task["conversation_id"],task["id"],task.get("org_id"))
        except TaskQueueFullError:
            return
        token=str(uuid4())
        try:
            claim=await self.rpc(task,"claim_chat_image_submission",p_claim_token=token,
                p_lease_seconds=self.settings.chat_image_submission_lease_seconds)
        except BaseException:
            # Commit may have succeeded. Never treat an RPC acknowledgement
            # error as permission to recreate a paid task.
            raise
        if claim.get("outcome") != "claimed":
            if claim.get("outcome") in {"identity_denied","insufficient_credits"}:
                await self.definite_failure(task,"当前权限或积分不足，未提交供应商")
                await limiter.release(task["user_id"],task["conversation_id"],org_id=task.get("org_id"),slot_id=task["id"])
            return
        adapter=None
        try:
            # Final exact versions immediately before the durable send marker.
            await asyncio.to_thread(resolver.verify,snapshot["references"])
            adapter=create_image_adapter(snapshot["model"],shadow_user_id=origin["workspace_owner_id"],shadow_org_id=task.get("org_id"))
            marker=await self.rpc(task,"mark_chat_image_dispatch",p_claim_token=token)
            if marker.get("outcome") != "dispatch":
                return
            result=await adapter.generate(prompt=snapshot["prompt"],image_urls=urls or None,
                size=snapshot["aspect_ratio"],resolution=snapshot["resolution"],output_format=snapshot["output_format"],
                callback_url=ImageHandler(self.scoped(task))._build_callback_url("kie"),
                **({"background":snapshot["background"]} if "background" in snapshot else {}),
                wait_for_result=False,_chat_image_single_submit=True)
            if not result.task_id:
                raise KieSubmissionUncertainError("Missing acknowledgement")
            bound=await self.rpc(task,"bind_chat_image_submission",p_claim_token=token,p_external_task_id=result.task_id)
            if bound.get("outcome") != "accepted":
                await self.rpc(task,"mark_chat_image_uncertain",p_claim_token=token,
                    p_timeout_seconds=self.settings.chat_image_uncertain_timeout_seconds)
        except BaseException as error:
            # Once the send marker commits, unknown exceptions/cancellation
            # cannot prove that the supplier rejected the paid request.
            if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)):
                raise
            current_task=await self.refresh(task)
            state=current_task["request_params"][LIFECYCLE_KEY]
            from services.adapters.kie.client import KieAPIError
            definitely_rejected=isinstance(error,KieAPIError) and error.status_code is not None and 400<=int(error.status_code)<500
            if definitely_rejected:
                await self.rpc(current_task,"record_chat_image_provider_result",p_result={"status":"failed","error":"供应商明确拒绝生成请求"})
                await self.finish_with_lock(await self.refresh(current_task))
            elif not state.get("dispatch_started_at"):
                await self.definite_failure(current_task,"生成请求校验或供应商明确拒绝")
            elif state["phase"] == "submitting":
                await self.rpc(task,"mark_chat_image_uncertain",p_claim_token=token,
                    p_timeout_seconds=self.settings.chat_image_uncertain_timeout_seconds)
            logger.warning("chat_image_submit_failed | task={} | error_type={}",task["id"],type(error).__name__)
        finally:
            if adapter is not None:
                await adapter.close()

    async def definite_failure(self, task, error):
        state=task["request_params"][LIFECYCLE_KEY]
        outcome=await self.rpc(task,"fail_chat_image_before_dispatch",p_error=error,
            p_expected_phase=state["phase"],p_claim_token=state.get("claim_token"))
        if outcome.get("outcome")=="settling":
            await self.finish_with_lock(await self.refresh(task))

    async def record_provider_result(self, task, result):
        payload={"image_urls":getattr(result,"image_urls",[]),"error":getattr(result,"fail_msg",None) or "生成失败"}
        from services.adapters.base import TaskStatus
        payload["status"]="success" if result.status==TaskStatus.SUCCESS else "failed"
        await self.rpc(task,"record_chat_image_provider_result",p_result=payload)
        await self.finish_with_lock(await self.refresh(task))

    async def finish_with_lock(self, task):
        """Reuse completion lock and renewal for callback/recovery convergence."""
        from core.redis import RedisClient
        from services.task_completion_service import TaskCompletionService
        key=f"chat_image_settlement:{task['id']}"
        token=await RedisClient.acquire_lock(key,timeout=300)
        if not token:
            return
        renewal=asyncio.create_task(TaskCompletionService(self.db)._renew_completion_lock(key,token))
        try:
            await self.finish_recorded(await self.refresh(task))
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError,Exception):
                await renewal
            await RedisClient.release_lock(key,token)

    async def finish_recorded(self, task):
        result=task.get("result") or {}
        if task["request_params"][LIFECYCLE_KEY]["phase"]=="published":
            await self.deliver(task)
            return
        if result.get("status") != "success":
            error=result.get("error") or "生成失败"
            await self.publish(task,[{"type":"image","url":None,"failed":True,"error":error}],
                "cancelled" if result.get("cancelled") else "failed",error)
            return
        from services.file_upload import persist_media_urls_to_workspace
        from services.assets.asset_registry import register_task_media_best_effort
        snapshot=task["request_params"][REQUEST_KEY]
        content=task.get("result_data")
        if not content:
            urls=result.get("image_urls") or []
            if len(urls)!=1:
                await self.publish(task,[{"type":"image","url":None,"failed":True,"error":"供应商单图结果不符合合同"}],"failed","供应商单图结果不符合合同")
                return
            payloads=await persist_media_urls_to_workspace(urls,snapshot["origin"]["workspace_owner_id"],task.get("org_id"),
                media_type="image",meta={"task_id":task["id"],"prompt":snapshot["prompt"],"model":snapshot["model"]})
            if not payloads or not payloads[0].get("workspace_path"):
                # A temporary provider URL is retained in result; only save it
                # again on recovery, never recreate a provider task.
                raise RuntimeError("CHAT_IMAGE_RESULT_STORAGE_PENDING")
            content={**payloads[0],"type":"image","task_id":task["id"],"source_task_id":snapshot.get("source_task_id")}
            content.pop("kind",None)
            await asyncio.to_thread(lambda:self.scoped(task).table("tasks").update({"result_data":content}).eq("id",task["id"]).execute())
        origin=snapshot["origin"]
        owner=SimpleNamespace(db=self.scoped(task),user_id=task["user_id"],workspace_user_id=origin["workspace_owner_id"],
            org_id=task.get("org_id"),context_scope=origin["context_scope"],conversation_id=task["conversation_id"],
            resource_manifest=None,execution_mode="interactive")
        from services.file_resources import FileTarget, FileTargetError, FileTargetResolver
        try:
            # Cached and newly saved ordinary images also require an actual file.
            await asyncio.to_thread(lambda:FileTarget(FileTargetResolver(owner).guarded(content["workspace_path"])).validate())
            if snapshot.get("background")=="transparent":
                checks=await asyncio.to_thread(inspect_transparent_result,owner,content["workspace_path"])
        except (FileTargetError,FileNotFoundError) as error:
            if isinstance(error,FileNotFoundError) or error.code=="RESOURCE_NOT_FOUND":
                # Replay the recorded download, never generation or a fuzzy path.
                await asyncio.to_thread(lambda:self.scoped(task).table("tasks").update({"result_data":None}).eq("id",task["id"]).execute())
            raise
        except ValueError as error:
            if str(error)!="IMAGE_TRANSPARENCY_OUTPUT_INVALID":
                raise
            checks={"has_transparency":False}
        if snapshot.get("background")=="transparent":
            if not checks["has_transparency"]:
                await self.publish(task,[{"type":"image","url":None,"failed":True,
                    "error":"供应商结果没有真实透明像素，积分已退还；平台记录本次成本"}],"failed","透明背景输出合同未满足")
                return
            content.update(checks)
        if snapshot.get("size_requirement"):
            from services.handlers.image_dimensions import read_image_dimensions, output_size_check
            path = FileTargetResolver(owner).guarded(content["workspace_path"])
            facts = None
            try:
                facts = await asyncio.to_thread(read_image_dimensions, path)
                checks = output_size_check(facts, snapshot["size_requirement"])
            except ValueError:
                checks = {"target": snapshot["size_requirement"], "size_matches": False,
                          "error": "IMAGE_DIMENSIONS_UNAVAILABLE"}
            content.update({key: facts[key] for key in ("width", "height")} if facts else {})
            content["quality_checks"] = {**content.get("quality_checks", {}), **checks}
            if not checks["size_matches"]:
                error = "生成结果未满足尺寸要求"
                content.update(failed=True, error=error)
                # Existing output-contract failure settlement, no new refund
                # policy, paid retry, stretching or cropping.
                await self.publish(task, [content], "failed", error)
                return
        registered=await asyncio.to_thread(register_task_media_best_effort,self.scoped(task),task=task,content_parts=[content])
        if not registered:
            raise RuntimeError("CHAT_IMAGE_ASSET_REGISTRATION_PENDING")
        content["asset_id"]=str(registered[0]["asset"]["id"])
        await self.publish(task,[content],"completed")

    async def publish(self, task, content, status, error="",*,platform_pays=False):
        snapshot = task["request_params"][REQUEST_KEY]
        content = [{**part, "task_id":task["id"], "source_task_id": snapshot.get("source_task_id")
            or snapshot["origin"].get("retry_of_task_id")} for part in content]
        await self.rpc(task,"publish_chat_image_result",p_content=content,p_status=status,p_error=error,p_platform_pays=platform_pays)
        await self.deliver(await self.refresh(task))

    async def deliver(self, task):
        from schemas.websocket import build_message_done
        from services.websocket_manager import ws_manager
        from api.deps import get_task_limit_service
        if task["request_params"][REQUEST_KEY]["origin"].get("destination")=="skill_trial":
            limiter=await get_task_limit_service()
            await limiter.release(task["user_id"],task["conversation_id"],org_id=task.get("org_id"),slot_id=task["id"])
            await self.rpc(task,"ack_chat_image_delivery")
            return
        message=await asyncio.to_thread(lambda:self.scoped(task).table("messages").select("*").eq("id",task["assistant_message_id"]).single().execute().data)
        if isinstance(message.get("content"),str):
            message["content"]=json.loads(message["content"])
        await ws_manager.send_to_task_or_user(task["id"],task["user_id"],build_message_done(task["id"],task["conversation_id"],message,task.get("credits_used",0)),org_id=task.get("org_id"))
        limiter=await get_task_limit_service()
        await limiter.release(task["user_id"],task["conversation_id"],org_id=task.get("org_id"),slot_id=task["id"])
        # Update only our delivery fact, preserving immutable snapshot and phases.
        await self.rpc(task,"ack_chat_image_delivery")
