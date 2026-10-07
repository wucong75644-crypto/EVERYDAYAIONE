"""Persistence-first three-stage ecommerce-image planner.

All model calls remain inside ModelGateway. The parent chat task supplies the
trusted input boundary, and a fenced SQL lease protects resumable stage state.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from uuid import uuid4

from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from services.agent.agent_result import AgentResult
from services.model_gateway import ModelCallRequest, get_model_gateway

from .contracts import parse_json, source_id, text_hash, validate_images, validate_product
from .recovery import attach_receipt, error_facts, failure_summary, PlannerRecoveryError
from .workflow import binding, store_binding, WorkflowBinding, KEY
from .prompt_resources import HASHES, INTEGRATION_RULES_SHA256, SCHEMA_SHA256, resources, wrapper


def _content(row):
    value = row.get("content") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [{"type": "text", "text": value}]
    return value if isinstance(value, list) else []


def _raw_parts(row):
    return [(index, part.get("text", "")) for index, part in enumerate(_content(row))
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)]


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


class EcommerceImagePlanner:
    def __init__(self, owner):
        self.owner = owner
        self.settings = __import__("core.config", fromlist=["get_settings"]).get_settings()
        self.scope = ScopedDatabaseClient(owner.db, DatabaseScope(
            owner.user_id, owner.org_id, DatabaseAccessKind.RUNTIME,
            request_id=f"ecom-plan:{owner.task_id}"[:128]))

    async def run(self, args):
        try:
            result = await self._run(args)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code, category, _retry = error_facts(error)
            result = AgentResult(failure_summary(code, category), status="error", error_message=code)
            return attach_receipt(result, category=category)
        if "retry_context" not in result.metadata:
            attach_receipt(result, action=result.metadata.pop("recovery_action", None))
        return result

    async def _bind_plan(self, plan_id):
        value = self.workflow or WorkflowBinding(kind="main_images", root_task_id=self.owner.task_id,
            window_task_id=self.owner.task_id, generation_run_id=str(uuid4()))
        value.plan_id = plan_id
        self.workflow = await store_binding(self.scope, self.owner.task_id, self.owner.image_execution_token, value)

    async def _run(self, args):
        if not any(entry.get("skill_key") == "ecommerce-main-images"
                for entry in getattr(self.owner, "image_skill_snapshot", ())):
            return AgentResult("请先调用 activate_skill，skill_id 使用 ecommerce-main-images，读取正文后再调用主图策划工具。",
                status="error", error_message="ECOM_PLAN_SKILL_REQUIRED")
        if self.settings.ecom_image_planning_enabled is not True:
            return AgentResult("主图策划服务尚未启用。", status="error", error_message="ECOM_IMAGE_PLANNING_DISABLED")
        rates = (self.settings.ecom_image_planning_input_credits_per_million,
                 self.settings.ecom_image_planning_output_credits_per_million)
        if not all(rate and rate > 0 for rate in rates):
            return AgentResult("主图策划计费尚未配置，当前无法调用策划模型。", status="error",
                error_message="ECOM_IMAGE_PLANNING_BILLING_UNCONFIGURED")
        model = self.settings.ecom_image_planning_model
        from services.adapters.factory import MODEL_REGISTRY
        registered = MODEL_REGISTRY.get(model)
        if model != "gpt-5-6-luna" or not registered or registered.provider.value != "kie" or registered.provider_model != model:
            return AgentResult("策划模型配置与已注册的 KIE GPT 5.6 Luna 不匹配。", status="error",
                error_message="ECOM_IMAGE_PLANNING_MODEL_MISMATCH")
        if (set(args) - {"references", "source_message_ids", "image_count", "task_type", "continue_plan_id"}
                or args.get("task_type", "main_images") != "main_images"):
            return AgentResult("当前仅支持主图策划，请检查输入范围。", status="error", error_message="ECOM_IMAGE_PLAN_ARGUMENTS_INVALID")
        continue_plan_id = args.get("continue_plan_id")
        if continue_plan_id is not None:
            try:
                continue_plan_id = str(__import__("uuid").UUID(continue_plan_id))
            except (TypeError, ValueError, AttributeError):
                return AgentResult("续接的方案编号无效，请重新发起策划。", status="error", error_message="ECOM_PLAN_CONTINUATION_INVALID")
            if "references" in args or "source_message_ids" in args:
                return AgentResult("续接方案会沿用已核验的原图顺序，请不要在同一次调用中替换引用。", status="error",
                    error_message="ECOM_PLAN_CONTINUATION_INPUT_CONFLICT")
        count = args.get("image_count")
        if count is not None and (type(count) is not int or not 1 <= count <= 15):
            return AgentResult("主图数量需为 1 至 15 张。", status="error", error_message="ECOM_IMAGE_COUNT_INVALID")
        if continue_plan_id is None and "references" not in args:
            return AgentResult("首次策划需要传入真实商品参考图。", status="error", error_message="ECOM_PLAN_REFERENCES_REQUIRED")
        call_id = getattr(self.owner, "_current_tool_call_id", None)
        from services.tools.dispatcher import current_dispatch_call_id
        call_id = current_dispatch_call_id() or call_id
        if not call_id or not self.owner.task_id or self.owner.context_scope != "user" or self.owner.user_id != self.owner.workspace_user_id:
            return AgentResult("当前聊天没有可用于策划的可信任务上下文。", status="error", error_message="ECOM_PLAN_TRUSTED_CONTEXT_REQUIRED")
        if self.owner.cancellation_event is not None and self.owner.cancellation_event.is_set():
            raise asyncio.CancelledError()
        parent = await asyncio.to_thread(lambda: self.scope.table("tasks").select(
            "id,user_id,org_id,conversation_id,turn_id,input_message_id,base_context_revision,request_params,status"
        ).eq("id", self.owner.task_id).single().execute().data)
        if (parent.get("user_id") != self.owner.user_id or parent.get("org_id") != self.owner.org_id
                or parent.get("conversation_id") != self.owner.conversation_id or parent.get("status") != "running"
                or parent.get("base_context_revision") is None or not parent.get("input_message_id")):
            return AgentResult("聊天任务的原始消息边界不可用，未开始策划。", status="error", error_message="ECOM_PLAN_PARENT_DENIED")
        if self.owner.execution_mode != "interactive":
            return AgentResult("主图策划仅可从当前交互聊天发起。", status="error", error_message="ECOM_PLAN_MODE_DENIED")

        self.workflow = binding((parent.get("request_params") or {}).get(KEY))
        if self.workflow and self.workflow.plan_id and not continue_plan_id:
            return AgentResult("当前主图任务已有方案，请使用返回的 continue_plan_id 续接，不能重新传图绕过已有阶段。",
                status="error", error_message="ECOM_PLAN_CONTINUATION_REQUIRED",
                metadata={"plan_id": self.workflow.plan_id, "recovery_action": "resume_plan"})
        if self.workflow and self.workflow.plan_id and continue_plan_id and self.workflow.plan_id != continue_plan_id:
            return AgentResult("续接编号与当前主图任务不一致。", status="error", error_message="ECOM_PLAN_CONTINUATION_DENIED")
        previous_plan = None
        if continue_plan_id:
            previous_plan = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").select(
                "id,user_id,org_id,conversation_id,parent_task_id,input_message_id,base_context_revision,"
                "plan_revision,status,current_stage,image_count,input_snapshot,stage_outputs,target_size,root_plan_id,recovery_state,prompt_versions,model_settings"
            ).eq("id", continue_plan_id).maybe_single().execute().data)
            if (not previous_plan or previous_plan.get("user_id") != self.owner.user_id
                    or previous_plan.get("org_id") != self.owner.org_id
                    or previous_plan.get("conversation_id") != self.owner.conversation_id
                    or previous_plan.get("status") not in {"needs_input", "insufficient", "failed", "ready"}):
                return AgentResult("没有找到可续接的本会话方案；请重新发起主图策划。", status="error",
                    error_message="ECOM_PLAN_CONTINUATION_DENIED")
            if (self.workflow and self.workflow.mode == "new" and not self.workflow.plan_id
                    and previous_plan["parent_task_id"] != self.owner.task_id):
                return AgentResult("当前用户请求尚未与该历史主图任务关联，请先明确继续原任务。", status="error",
                    error_message="ECOM_PLAN_CONTINUATION_DENIED")
            if previous_plan["parent_task_id"] == self.owner.task_id and count is not None and count != previous_plan["image_count"]:
                return AgentResult("同一回合恢复不能改变已保存的主图张数，请等待用户提出新的修改要求。", status="error",
                    error_message="ECOM_PLAN_CONTINUATION_INPUT_CONFLICT")
        if previous_plan and previous_plan["status"] in {"failed", "ready"}:
            mode = self.workflow.mode if self.workflow else None
            category = (previous_plan.get("recovery_state") or {}).get("last_error", {}).get("category")
            if previous_plan["status"] == "ready" and mode not in {"retry", "regenerate", "revise"}:
                return AgentResult("已完成方案需要明确继续或重新生成的用户指令。", status="error", error_message="ECOM_PLAN_CONTINUATION_DENIED")
            if previous_plan["status"] == "failed" and not (self.workflow and self.workflow.reset_window) and category not in {"transient_rejection", "output_validation"}:
                return AgentResult("此方案无法自动重发，请先处理错误并明确重试。", status="error", error_message="ECOM_PLAN_EXECUTION_UNCERTAIN")
        count = count if count is not None else previous_plan["image_count"] if previous_plan else 10

        from services.handlers.chat_context.image_sources import content_parts, legacy_catalog
        from services.handlers.chat_image_request import ChatImageInputResolver
        resolver = ChatImageInputResolver(self.owner, base_revision=parent["base_context_revision"],
            input_message_id=str(parent["input_message_id"]), legacy_sources=legacy_catalog(parent), require_known_sources=True)
        input_row = await asyncio.to_thread(resolver._message, str(parent["input_message_id"]))
        if input_row.get("role") != "user":
            return AgentResult("当前回合缺少用户原始消息。", status="error", error_message="ECOM_PLAN_INPUT_UNAVAILABLE")
        current_message_id = str(parent["input_message_id"])
        if previous_plan:
            previous_messages = previous_plan.get("input_snapshot", {}).get("messages", [])
            if not isinstance(previous_messages, list) or any(not isinstance(row, dict) for row in previous_messages):
                return AgentResult("已保存方案的原始消息记录不可用，请重新发起策划。", status="error",
                    error_message="ECOM_PLAN_CONTINUATION_SNAPSHOT_INVALID")
            messages = list(previous_messages)
            if (previous_plan["status"] in {"needs_input", "insufficient"}
                    and any(row.get("message_id") == current_message_id for row in messages)):
                await self._bind_plan(previous_plan["id"])
                return await self._result(previous_plan["id"], previous_plan["plan_revision"], previous_plan["status"])
            if (not (self.workflow and self.workflow.mode in {"retry", "regenerate"})
                    and not any(row.get("message_id") == current_message_id for row in messages)):
                messages.append({"message_id": current_message_id, "context_revision": input_row.get("context_revision"), "parts": [
                    {"content_index": i, "text": value} for i, value in _raw_parts(input_row)]})
            references_in = [{key: value for key, value in ref.items() if key != "source_id"}
                for ref in previous_plan.get("input_snapshot", {}).get("references", [])]
        else:
            source_ids = args.get("source_message_ids", [])
            if (not isinstance(source_ids, list) or len(source_ids) > 8
                    or any(not isinstance(item, str) for item in source_ids)
                    or len(set(source_ids)) != len(source_ids)
                    or current_message_id in source_ids):
                return AgentResult("历史文字消息引用无效。", status="error", error_message="ECOM_PLAN_HISTORY_INVALID")
            messages = []
            for message_id in [*source_ids, current_message_id]:
                row = input_row if message_id == current_message_id else await asyncio.to_thread(resolver._message, message_id)
                if row.get("role") != "user":
                    raise PermissionError("ECOM_PLAN_USER_TEXT_ONLY")
                messages.append({"message_id": message_id, "context_revision": row.get("context_revision"), "parts": [
                    {"content_index": i, "text": value} for i, value in _raw_parts(row)]})
            references_in = args.get("references", [])
        if not isinstance(references_in, list) or not 1 <= len(references_in) <= 16:
            return AgentResult("请先选择至少一张真实商品参考图。", status="error", error_message="ECOM_PLAN_REFERENCES_REQUIRED")
        refs = await asyncio.to_thread(resolver.resolve, references_in)
        await asyncio.to_thread(resolver.verify, refs)
        for reference in refs:
            reference["source_id"] = source_id(reference)
        if previous_plan:
            old_refs = {item["source_id"]: item for item in previous_plan["input_snapshot"]["resolved_references"]}
            if (set(old_refs) != {reference["source_id"] for reference in refs}
                    or any(old_refs[reference["source_id"]]["content_sha256"] != reference["content_sha256"]
                        or old_refs[reference["source_id"]]["file_version"] != reference["file_version"]
                        or old_refs[reference["source_id"]]["workspace_path"] != reference["workspace_path"]
                        for reference in refs)):
                return AgentResult("方案原图或版本已变化，不能把新图替换进旧方案；请重新发起策划。", status="error",
                    error_message="ECOM_PLAN_REFERENCE_CHANGED")
        from services.handlers.image_size_requirements import ImageSizeError, resolve_size_requirement
        try:
            intent, previous = await asyncio.to_thread(resolver.size_context)
            if previous_plan:
                previous = {**(previous or {}), **previous_plan["target_size"]}
            size_refs = [ref for ref in refs if (ref["role"].strip().lower() in {
                "product", "product_image", "product_detail", "商品", "商品图", "商品参考"
            } or ref["role"].strip().startswith(("商品", "产品")))]
            if not size_refs:
                return AgentResult("请把至少一张参考图标记为商品图，风格参考图请标记为风格图。", status="error",
                    error_message="ECOM_PLAN_PRODUCT_REFERENCE_REQUIRED")
            normalized, target_size = resolve_size_requirement({}, size_refs, intent=intent, previous=previous)
        except ImageSizeError as error:
            questions = [error.guidance]
            return AgentResult(json.dumps({"status":"needs_input","questions":questions,
                "reason":error.code,"message":"补充规格后重新发起策划；本次尚未调用策划模型。"},ensure_ascii=False),
                status="success",metadata={"status":"needs_input","questions":questions,"plan_id":None})
        if target_size.get("aspect_ratio") == "auto":
            return AgentResult("当前生图入口没有确定画布比例，请先明确支持的比例。", status="error", error_message="ECOM_PLAN_CANVAS_UNRESOLVED")
        from services.handlers.chat_image_request import default_chat_image_model
        from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
        image_config = IMAGE_MODEL_CONFIGS[default_chat_image_model("image_to_image")]
        target_size["resolution"] = normalized.get("resolution") or (
            "1K" if image_config.get("supports_resolution") else None)

        public_refs = [{**{k: r[k] for k in ("resource_ref", "file_id", "asset_id", "message_id", "content_index") if k in r},
            "role": r["role"]} for r in refs]
        for public, full in zip(public_refs, refs):
            public["source_id"] = full["source_id"]
        image_urls = []
        for reference in refs:
            image_urls.append(await asyncio.to_thread(resolver.preview, reference))
        input_snapshot = {"messages": messages, "references": public_refs,
            "resolved_references": [{"source_id": r["source_id"], "content_sha256": r["content_sha256"],
                "file_version": r["file_version"], "workspace_path": r["workspace_path"]} for r in refs],
            "image_count": count, "task_type": "main_images", "target_size": target_size,
            **({"supersedes_plan_id": previous_plan["id"]} if previous_plan else {})}
        prompt_bodies, schema = resources()
        versions = {"resources_sha256": list(HASHES), "schema_sha256": SCHEMA_SHA256,
            "integration_reference_sha256": INTEGRATION_RULES_SHA256}
        model_settings = {"provider": "kie", "model": model, "reasoning_effort": self.settings.ecom_image_planning_reasoning}
        reusable = (previous_plan and previous_plan.get("prompt_versions") == versions
            and previous_plan.get("model_settings") == model_settings)
        same_canvas = previous_plan and all(previous_plan["target_size"].get(key) == target_size.get(key)
            for key in ("aspect_ratio", "resolution"))
        if (previous_plan and previous_plan["status"] == "ready" and self.workflow
                and self.workflow.mode in {"retry", "regenerate"} and count == previous_plan["image_count"]
                and reusable and same_canvas):
            await self._bind_plan(previous_plan["id"])
            return await self._result(previous_plan["id"], previous_plan["plan_revision"], "ready")
        invocation_key = _digest(input_snapshot)
        prior = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").select(
            "id,plan_revision,status,current_stage,stage_outputs,stage_attempts,items,review_records,root_plan_id,recovery_state")
            .eq("parent_task_id", self.owner.task_id).eq("invocation_key", invocation_key).maybe_single().execute().data)
        if prior:
            if prior["status"] == "ready":
                await self._bind_plan(prior["id"])
                return await self._result(prior["id"], prior["plan_revision"], prior["status"])
            if prior["status"] in {"needs_input","insufficient"}:
                return await self._result(prior["id"], prior["plan_revision"], prior["status"])
            if prior["status"] not in {"planning", "failed"}:
                return AgentResult("这份主图方案不能继续处理，请重新发起策划。", status="error",
                    error_message="ECOM_PLAN_STATE_NOT_RESUMABLE")
            row = prior
        else:
            resume_outputs = {}
            resume_stage = 1
            if reusable:
                old_outputs = previous_plan.get("stage_outputs") or {}
                reuse = self.workflow.reuse_through_stage if self.workflow else 0
                if previous_plan["parent_task_id"] == self.owner.task_id:
                    reuse = 2
                if not self.workflow and previous_plan["status"] in {"needs_input", "insufficient"} and previous_plan.get("current_stage") == 3:
                    reuse = 2
                if reuse and old_outputs.get("1", {}).get("status") == "ready":
                    validate_product(old_outputs["1"], schema, input_snapshot)
                    resume_outputs["1"] = old_outputs["1"]
                    resume_stage = 2
                    if reuse == 2 and same_canvas and isinstance(old_outputs.get("2"), str):
                        self._validate_visual(old_outputs["2"], __import__("services.agent.image.ecommerce_planner.contracts", fromlist=["VISUAL_SECTIONS"]).VISUAL_SECTIONS)
                        resume_outputs["2"] = old_outputs["2"]
                        resume_stage = 3
            row = {"id": str(uuid4()), "user_id": self.owner.user_id, "org_id": self.owner.org_id,
                "conversation_id": self.owner.conversation_id, "parent_task_id": self.owner.task_id,
                "input_message_id": str(parent["input_message_id"]), "base_context_revision": parent["base_context_revision"],
                "invocation_key": invocation_key, "input_digest": invocation_key,
                "plan_revision": previous_plan["plan_revision"] + 1 if previous_plan else 1,
                "supersedes_plan_id": previous_plan["id"] if previous_plan else None,
                "root_plan_id": (previous_plan.get("root_plan_id") or previous_plan["id"]) if previous_plan else None,
                "status": "planning", "current_stage": resume_stage,
                "image_count": count, "input_snapshot": input_snapshot,
                "stage_outputs": resume_outputs,
                "prompt_versions": versions,
                "model_settings": model_settings,
                "target_size": target_size}
            try:
                await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").insert(row).execute())
            except Exception:
                raced = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").select(
                    "id,plan_revision,status,current_stage,stage_outputs,stage_attempts,items,review_records,root_plan_id,recovery_state")
                    .eq("parent_task_id", self.owner.task_id).eq("invocation_key", invocation_key).maybe_single().execute().data)
                if raced:
                    return await self._result(raced["id"], raced["plan_revision"], raced["status"])
                raise
        await self._bind_plan(row["id"])
        active_stage = int(row.get("current_stage", 1))
        lease = str(uuid4())
        claimed = await asyncio.to_thread(lambda: self.scope.rpc("claim_ecom_image_plan", {
            "p_plan_id": row["id"], "p_parent_task_id": self.owner.task_id, "p_lease_token": lease,
            "p_lease_seconds": min(600, max(10, math.ceil(self.settings.ecom_image_planning_stage_timeout * 3 + 20))),
        }).execute().data)
        if not claimed.get("claimed"):
            return AgentResult("主图方案正在由另一个请求处理。", status="error", error_message="ECOM_PLAN_LEASE_BUSY",
                metadata={"plan_id": row["id"]})
        stage_outputs = claimed.get("stage_outputs") or {}
        current_stage = int(claimed.get("current_stage", 1))
        evidence = {"input_snapshot": input_snapshot, "product_schema": schema}
        if previous_plan:
            evidence["previous_plan"] = {"plan_id": previous_plan["id"],
                "plan_revision": previous_plan["plan_revision"], "status": previous_plan["status"],
                "stage_outputs": previous_plan.get("stage_outputs") or {}}
        try:
            first = stage_outputs.get("1")
            if first is None:
                first, usage = await self._stage(row, lease, 1, prompt_bodies[0], wrapper(1), evidence, messages, refs, image_urls,
                    validator=lambda value: validate_product(value, schema, input_snapshot))
                await self._save(row, lease, 1, first, "planning" if first["status"] == "ready" else first["status"], None, usage)
            else:
                validate_product(first, schema, input_snapshot)
            if first["status"] != "ready":
                return await self._result(row["id"], row["plan_revision"], first["status"])
            evidence["product_selling_points"] = first
            active_stage = max(2, current_stage)
            visual_sections = __import__("services.agent.image.ecommerce_planner.contracts", fromlist=["VISUAL_SECTIONS"]).VISUAL_SECTIONS
            second = stage_outputs.get("2")
            if second is None:
                second, usage = await self._stage(row, lease, 2, prompt_bodies[1], wrapper(2), evidence, messages, refs, image_urls,
                    validator=lambda value: self._validate_visual(value, visual_sections))
                await self._save(row, lease, 2, second, "planning", None, usage)
            else:
                self._validate_visual(second, visual_sections)
            evidence["visual_direction"] = second
            active_stage = 3
            final = stage_outputs.get("3")
            if final is None:
                final, usage = await self._stage_images(row, lease, prompt_bodies[2], wrapper(3), evidence, messages, refs, image_urls,
                    validator=lambda value: validate_images(value, input_snapshot))
            else:
                # Stage output and ready/needs_input state commit together. A
                # planning row with a final output is inconsistent, not a new
                # billable attempt or permission to invent a settlement.
                raise PlannerRecoveryError("ECOM_PLAN_EXECUTION_UNCERTAIN")
            if final["status"] != "ready":
                await self._save(row, lease, 3, final, final["status"], None, usage)
                return await self._result(row["id"], row["plan_revision"], final["status"])
            await self._save(row, lease, 3, final, "ready", final, usage)
            return await self._result(row["id"], row["plan_revision"], "ready")
        except asyncio.CancelledError:
            await self._fail(row["id"], lease, active_stage, "cancelled")
            raise
        except Exception as error:
            saved = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").select("stage_outputs,status")
                .eq("id", row["id"]).single().execute().data)
            if saved.get("status") == "ready":
                return await self._result(row["id"], row["plan_revision"], "ready")
            await self._fail(row["id"], lease, active_stage, "failed", error)
            code, category, _retry = error_facts(error)
            preserved = [i for i in (1, 2) if str(i) in saved.get("stage_outputs", {})]
            result = AgentResult(failure_summary(code, category, active_stage, preserved), status="error", error_message=code,
                metadata={"plan_id": row["id"], "stage": "failed"})
            return attach_receipt(result, failed_stage=active_stage, preserved=preserved, category=category)

    async def _stage(self, row, lease, stage, original, integration, evidence, messages, refs, image_urls, validator=None):
        last_error = None
        for repair in range(3):
            prompt = integration + "\n\n【以下为随版本发布的完整专业阶段规则】\n" + original
            if repair:
                prompt += f"\n\n你上一稿未通过服务端校验（{last_error}）。修正该项，保留全部有效内容后重新完整输出。"
            body = {"stage": stage, "input_snapshot": evidence["input_snapshot"],
                "stage_evidence": {k:v for k,v in evidence.items() if k != "input_snapshot"},
                "raw_user_messages": messages,
                "references_in_generation_order": [{"ordinal": n, "source_id": r["source_id"],
                    "role": r["role"]} for n,r in enumerate(refs, 1)]}
            user_content = [{"type": "input_text", "text": json.dumps(body, ensure_ascii=False)}]
            for i, (reference, url) in enumerate(zip(refs, image_urls), 1):
                user_content.extend([{"type": "input_text", "text": f"参考图片{i} | 来源ID={reference['source_id']} | 角色={reference['role']}"},
                    {"type": "input_image", "image_url": url}])
            result, usage = await self._call(row, lease, stage, prompt,
                [{"role":"developer","content":prompt},{"role":"user","content":user_content}])
            try:
                if not result.strip():
                    raise ValueError("ECOM_PLAN_EMPTY_OUTPUT")
                parsed = result if stage == 2 else parse_json(result)
                return (validator(parsed) if validator else parsed), usage
            except (ValueError, json.JSONDecodeError) as error:
                last_error = str(error)
                await self._save_attempt(row, lease, stage, {**usage,"validation_error":last_error}, "schema_repair", repair, charge=True)
        raise ValueError("PLANNER_JSON_VALIDATION_FAILED")

    async def _stage_images(self, row, lease, original, integration, evidence, messages, refs, image_urls, validator=None):
        from .contracts import ImagesOutput
        last_error = None
        for repair in range(3):
            # Keep the immutable professional rules, then give the final wire
            # contract so the source's Markdown UI does not replace JSON keys.
            prompt = original + "\n\n【最终平台交付协议，优先于上述展示格式】\n" + integration
            if repair:
                prompt += f"\n\n上一稿未通过服务端校验（{last_error}）。请保留首稿有效设计，修正问题后重新输出完整JSON。"
            body = {"stage":3,"input_snapshot":evidence["input_snapshot"],
                "product_selling_points":evidence["product_selling_points"],"visual_direction":evidence["visual_direction"],
                "raw_user_messages":messages,"references_in_generation_order":[
                    {"ordinal": n,"source_id":r["source_id"],"role":r["role"]} for n,r in enumerate(refs,1)],
                "output_json_schema": ImagesOutput.model_json_schema(),
                "reference_identity_examples": [{"source_id": r["source_id"],
                    "first_input_literal": f"输入图片1—{r['source_id']}"} for r in refs],
                **({"previous_plan": evidence["previous_plan"]} if evidence.get("previous_plan") else {})}
            user_content = [{"type":"input_text","text":json.dumps(body,ensure_ascii=False)}]
            for i,(reference,url) in enumerate(zip(refs,image_urls),1):
                user_content.extend([{"type":"input_text","text":f"参考图片{i} | 来源ID={reference['source_id']} | 角色={reference['role']}"},
                    {"type":"input_image","image_url":url}])
            output, usage = await self._call(row,lease,3,prompt,[{"role":"developer","content":prompt},{"role":"user","content":user_content}])
            try:
                if not output.strip():
                    raise ValueError("ECOM_PLAN_EMPTY_OUTPUT")
                parsed = parse_json(output)
                return (validator(parsed) if validator else parsed), usage
            except (ValueError,json.JSONDecodeError) as error:
                last_error=str(error)
                await self._save_attempt(row,lease,3,{**usage,"validation_error":last_error},"json_repair",repair,charge=True)
        raise ValueError("PLANNER_IMAGE_JSON_INVALID")

    async def _call(self, row, lease, stage, prompt, messages):
        while True:
            remaining = self.owner.execution_budget.remaining if self.owner.execution_budget else 600
            timeout = min(self.settings.ecom_image_planning_stage_timeout, remaining)
            if timeout <= 1:
                raise PlannerRecoveryError("ECOM_PLAN_PARENT_BUDGET_EXHAUSTED")
            attempt_id = str(uuid4())
            reservation = await self._reserve(row, lease, stage, attempt_id, remaining)
            if reservation.get("outcome") != "execute":
                raise PlannerRecoveryError("ECOM_PLAN_EXECUTION_UNCERTAIN")
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(reservation["deadline"].replace("Z", "+00:00"))
            timeout = min(timeout, (deadline - datetime.now(timezone.utc)).total_seconds())
            if timeout <= 1:
                await self._finish(row, lease, stage, attempt_id, {}, "rejected")
                raise PlannerRecoveryError("ECOM_PLAN_PARENT_BUDGET_EXHAUSTED")
            session = None
            content = ""
            tokens = {"input_tokens": 0, "output_tokens": 0, "provider_credits": None}
            try:
                session = get_model_gateway().open_chat(ModelCallRequest(
                    model_id=self.settings.ecom_image_planning_model, org_id=None,
                    task_id=self.owner.task_id, timeout=timeout, cancel_token=self.owner.cancellation_event,
                    budget=self.owner.execution_budget))
                async for chunk in session.stream_chat(messages, reasoning_effort=self.settings.ecom_image_planning_reasoning):
                    content += chunk.content or ""
                    tokens["input_tokens"] += chunk.prompt_tokens or 0
                    tokens["output_tokens"] += chunk.completion_tokens or 0
                    if chunk.credits_consumed is not None:
                        tokens["provider_credits"] = chunk.credits_consumed
                if session.last_result and session.last_result.status != "completed":
                    raise PlannerRecoveryError("ECOM_PLAN_EXECUTION_UNCERTAIN")
                if session.last_result:
                    usage = session.last_result.usage
                    tokens["input_tokens"] = max(tokens["input_tokens"], int(usage.get("prompt_tokens", 0)))
                    tokens["output_tokens"] = max(tokens["output_tokens"], int(usage.get("completion_tokens", 0)))
                    tokens["provider_credits"] = usage.get("api_credits", tokens["provider_credits"])
                credits = max(1, math.ceil(tokens["input_tokens"] * float(self.settings.ecom_image_planning_input_credits_per_million) / 1_000_000
                    + tokens["output_tokens"] * float(self.settings.ecom_image_planning_output_credits_per_million) / 1_000_000))
                return content, {**tokens, "user_credits": credits, "provider": "kie", "model": self.settings.ecom_image_planning_model,
                    "attempt_id": attempt_id}
            except asyncio.CancelledError:
                await self._finish(row, lease, stage, attempt_id, tokens, "uncertain")
                raise
            except Exception as error:
                code, category, safe = error_facts(error)
                diagnostics = {**tokens, "error_type": type(error).__name__, "error_code": code,
                    "http_status": getattr(error, "status_code", None), "provider_error_code": getattr(error, "error_code", None)}
                no_response = not content and not tokens["input_tokens"] and not tokens["output_tokens"] and tokens["provider_credits"] is None
                definite = no_response and (safe or category in {"authentication", "balance"})
                await self._finish(row, lease, stage, attempt_id, diagnostics, "rejected" if definite else "uncertain")
                if safe and definite and reservation["remaining_attempts"] > 0:
                    delay = min(2 ** (reservation["ordinal"] - 1), timeout / 4)
                    if self.owner.cancellation_event is not None:
                        try:
                            await asyncio.wait_for(self.owner.cancellation_event.wait(), delay)
                        except asyncio.TimeoutError:
                            pass
                        else:
                            raise asyncio.CancelledError()
                    else:
                        await asyncio.sleep(delay)
                    continue
                if not definite and category != "uncertain":
                    raise PlannerRecoveryError("ECOM_PLAN_EXECUTION_UNCERTAIN") from error
                raise
            finally:
                if session is not None:
                    await session.close()

    async def _reserve(self, row, lease, stage, attempt_id, remaining):
        return await asyncio.to_thread(lambda: self.scope.rpc("reserve_ecom_plan_attempt", {
            "p_plan_id": row["id"], "p_lease_token": lease, "p_attempt_id": attempt_id, "p_stage": stage,
            "p_wall_seconds": max(1, min(600, math.floor(remaining))),
        }).execute().data)

    async def _finish(self, row, lease, stage, attempt_id, usage, outcome, output=None, status="planning", final=None):
        from psycopg.types.json import Jsonb
        params = {"p_plan_id": row["id"], "p_lease_token": lease, "p_stage": stage, "p_attempt_id": attempt_id,
            "p_usage": usage, "p_outcome": outcome, "p_output": Jsonb(output) if isinstance(output, str) else output,
            "p_status": status, "p_items": final.get("images") if final else None,
            "p_reviews": final.get("review_records") if final else None,
            "p_credits": usage.get("user_credits", 0) if outcome in {"completed", "validation_failed"} else 0}
        return await asyncio.to_thread(lambda: self.scope.rpc("finish_ecom_plan_attempt", params).execute().data)

    async def _save_attempt(self,row,lease,stage,usage,status,repair,charge=False):
        if not usage.get("attempt_id"):
            raise PlannerRecoveryError("ECOM_PLAN_ATTEMPT_CONFLICT")
        return await self._finish(row, lease, stage, usage["attempt_id"], usage, "validation_failed")

    async def _call_store(self, row, lease, stage, output, status, final, usage=None):
        if usage is None:
            raise PlannerRecoveryError("ECOM_PLAN_ATTEMPT_CONFLICT")
        return await self._finish(row, lease, stage, usage["attempt_id"], usage, "completed", output, status, final)

    async def _save(self,row,lease,stage,output,status,final,usage=None):
        return await self._call_store(row,lease,stage,output,status,final,usage)

    async def _fail(self,plan_id,lease,stage,status,error=None):
        code, category, _retry = error_facts(error) if error is not None else ("MODEL_CANCELLED", "cancelled", False)
        try:
            await asyncio.to_thread(lambda: self.scope.rpc("fail_ecom_plan", {
                "p_plan_id": plan_id, "p_lease_token": lease, "p_stage": stage, "p_status": status,
                "p_error": {"code": code, "category": category},
            }).execute())
        except Exception:
            # An uncertain DB result cannot authorize another model call.
            from loguru import logger
            logger.warning("ecom_failure_record_uncertain | plan_id={} stage={}", plan_id, stage)

    async def _result(self,plan_id,revision,status):
        row=await asyncio.to_thread(lambda:self.scope.table("ecom_image_plans").select("id,plan_revision,status,current_stage,items,stage_outputs")
            .eq("id",plan_id).single().execute().data)
        if row["status"] == "ready":
            images=[{"item_id":i["item_id"],"position":i["position"],"name":i["name"],"purpose":i["purpose"]} for i in row["items"]]
            payload={"kind":"ecom_plan","plan_id":plan_id,"revision":row["plan_revision"],
                "product_insight":row["stage_outputs"]["1"]["product"].get("name") or "商品分析已完成",
                "visual_strategy":row["stage_outputs"]["2"],"images":images,"status":"ready"}
            if getattr(self, "workflow", None):
                receipts = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plan_acceptances").select("item_id,receipt")
                    .eq("generation_run_id", self.workflow.generation_run_id).eq("plan_id", plan_id)
                    .eq("plan_revision", row["plan_revision"]).execute().data)
                submitted = {str(entry["item_id"]): entry["receipt"] for entry in receipts or [] if entry.get("receipt")}
                images = [{**image, **({"submission_state": "submitted", "task_id": submitted[image["item_id"]]["task_id"],
                    "message_id": submitted[image["item_id"]]["message_id"]} if image["item_id"] in submitted else
                    {"submission_state": "not_submitted"})} for image in images]
            tool_result={"status":"ready","plan_id":plan_id,"revision":row["plan_revision"],
                "image_count":len(images),"images":images,
                "instruction":"按position顺序，仅为尚未submitted的项调用generate_image，只传对应plan_source；已有项使用原任务回执，不重发。"}
            return AgentResult(json.dumps(tool_result,ensure_ascii=False),
                status="success",emit_payloads=[payload],metadata={"plan_id":plan_id,"revision":row["plan_revision"],
                    "status":"ready","images":images})
        stage1=row["stage_outputs"].get("1",{})
        current=row["stage_outputs"].get(str(row.get("current_stage",1)),{})
        questions=stage1.get("questions",[]) if status=="needs_input" and row.get("current_stage",1)==1 else current.get("questions",[])
        gaps=stage1.get("gaps",[]) if status=="insufficient" else []
        return AgentResult(json.dumps({"status":status,"plan_id":plan_id,"revision":revision,"questions":questions,"gaps":gaps},ensure_ascii=False),
            status="success" if status in {"needs_input","insufficient"} else "error",
            metadata={"plan_id":plan_id,"revision":revision,"status":status,"questions":questions,"gaps":gaps})

    @staticmethod
    def _validate_visual(value, sections):
        if not isinstance(value, str):
            raise ValueError("PLANNER_VISUAL_SECTIONS_INVALID")
        positions = [value.find(f"## {index}. {title}") for index, title in enumerate(sections, 1)]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            raise ValueError("PLANNER_VISUAL_SECTIONS_INVALID")
        for index, position in enumerate(positions):
            end = positions[index + 1] if index + 1 < len(positions) else len(value)
            if not value[position:end].split("\n", 1)[-1].strip():
                raise ValueError("PLANNER_VISUAL_SECTION_EMPTY")
        return value
