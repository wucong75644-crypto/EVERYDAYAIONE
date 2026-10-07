"""Persistence-first three-stage ecommerce-image planner.

All model calls remain inside ModelGateway. The parent chat task supplies the
trusted input boundary, and a fenced SQL lease protects resumable stage state.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from decimal import Decimal
from uuid import uuid4

from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from services.agent.agent_result import AgentResult
from services.model_gateway import ModelCallRequest, get_model_gateway

from .contracts import parse_json, source_id, text_hash, validate_images, validate_product
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

        previous_plan = None
        if continue_plan_id:
            previous_plan = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").select(
                "id,user_id,org_id,conversation_id,parent_task_id,input_message_id,base_context_revision,"
                "plan_revision,status,current_stage,image_count,input_snapshot,stage_outputs,target_size"
            ).eq("id", continue_plan_id).maybe_single().execute().data)
            if (not previous_plan or previous_plan.get("user_id") != self.owner.user_id
                    or previous_plan.get("org_id") != self.owner.org_id
                    or previous_plan.get("conversation_id") != self.owner.conversation_id
                    or previous_plan.get("status") not in {"needs_input", "insufficient"}):
                return AgentResult("没有找到可续接的本会话方案；请重新发起主图策划。", status="error",
                    error_message="ECOM_PLAN_CONTINUATION_DENIED")
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
        invocation_key = _digest(input_snapshot)
        prior = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").select(
            "id,plan_revision,status,current_stage,stage_outputs,stage_attempts,items,review_records")
            .eq("parent_task_id", self.owner.task_id).eq("invocation_key", invocation_key).maybe_single().execute().data)
        if prior:
            if prior["status"] == "ready":
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
            if previous_plan:
                old_outputs = previous_plan.get("stage_outputs") or {}
                if (previous_plan.get("current_stage") == 3
                        and old_outputs.get("1", {}).get("status") == "ready"
                        and isinstance(old_outputs.get("2"), str)):
                    resume_outputs = {"1": old_outputs["1"], "2": old_outputs["2"]}
                    resume_stage = 3
            row = {"id": str(uuid4()), "user_id": self.owner.user_id, "org_id": self.owner.org_id,
                "conversation_id": self.owner.conversation_id, "parent_task_id": self.owner.task_id,
                "input_message_id": str(parent["input_message_id"]), "base_context_revision": parent["base_context_revision"],
                "invocation_key": invocation_key, "input_digest": invocation_key,
                "plan_revision": previous_plan["plan_revision"] + 1 if previous_plan else 1,
                "supersedes_plan_id": previous_plan["id"] if previous_plan else None,
                "status": "planning", "current_stage": resume_stage,
                "image_count": count, "input_snapshot": input_snapshot,
                "stage_outputs": resume_outputs,
                "prompt_versions": {"resources_sha256": list(HASHES), "schema_sha256": SCHEMA_SHA256,
                    "integration_reference_sha256": INTEGRATION_RULES_SHA256},
                "model_settings": {"provider": "kie", "model": model, "reasoning_effort": self.settings.ecom_image_planning_reasoning},
                "target_size": target_size}
            try:
                await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").insert(row).execute())
            except Exception:
                raced = await asyncio.to_thread(lambda: self.scope.table("ecom_image_plans").select(
                    "id,plan_revision,status,current_stage,stage_outputs,stage_attempts,items,review_records")
                    .eq("parent_task_id", self.owner.task_id).eq("invocation_key", invocation_key).maybe_single().execute().data)
                if raced:
                    return await self._result(raced["id"], raced["plan_revision"], raced["status"])
                raise
        active_stage = 1
        lease = str(uuid4())
        claimed = await asyncio.to_thread(lambda: self.scope.rpc("claim_ecom_image_plan", {
            "p_plan_id": row["id"], "p_parent_task_id": self.owner.task_id, "p_lease_token": lease,
            "p_lease_seconds": min(600, max(10, math.ceil(self.settings.ecom_image_planning_stage_timeout * 3 + 20))),
        }).execute().data)
        if not claimed.get("claimed"):
            return AgentResult("主图方案正在由另一个请求处理。", status="error", error_message="ECOM_PLAN_LEASE_BUSY")
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
                usage = None
            if final["status"] != "ready":
                await self._save(row, lease, 3, final, final["status"], None, usage)
                return await self._result(row["id"], row["plan_revision"], final["status"])
            await self._save(row, lease, 3, final, "ready", final, usage)
            return await self._result(row["id"], row["plan_revision"], "ready")
        except asyncio.CancelledError:
            await self._fail(row["id"], lease, active_stage, "cancelled")
            raise
        except Exception as error:
            await self._fail(row["id"], lease, active_stage, "failed", error)
            from services.adapters.kie.client import KieAuthenticationError
            summary = ("主图策划模型鉴权失败，需要修复 KIE 配置后重试。"
                if isinstance(error, KieAuthenticationError) else "主图策划服务调用失败，已保留阶段记录，请稍后重试。")
            return AgentResult(summary + "当前未完成策划，未按方案提交生图。", status="error",
                error_message=getattr(error, "code", None) or type(error).__name__,
                metadata={"plan_id": row["id"], "stage": "failed", "stop_workflow": True})

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
        last_error = None
        for repair in range(3):
            prompt = integration + "\n\n【以下为随版本发布的完整专业阶段规则】\n" + original
            if repair:
                prompt += f"\n\n上一稿未通过服务端校验（{last_error}）。请保留首稿有效设计，修正问题后重新输出完整JSON。"
            body = {"stage":3,"input_snapshot":evidence["input_snapshot"],
                "product_selling_points":evidence["product_selling_points"],"visual_direction":evidence["visual_direction"],
                "raw_user_messages":messages,"references_in_generation_order":[
                    {"ordinal": n,"source_id":r["source_id"],"role":r["role"]} for n,r in enumerate(refs,1)],
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
        await self._save_attempt(row,lease,stage,{"started":True},"started",None,charge=False)
        timeout = min(self.settings.ecom_image_planning_stage_timeout,
            max(1.0, self.owner.execution_budget.remaining) if self.owner.execution_budget else self.settings.ecom_image_planning_stage_timeout)
        if timeout <= 1:
            raise TimeoutError("ECOM_PLAN_PARENT_BUDGET_EXHAUSTED")
        session = get_model_gateway().open_chat(ModelCallRequest(
            # This platform Agent uses the platform KIE credential. Data access
            # and credit accounting continue to use self.scope/owner.org_id.
            model_id=self.settings.ecom_image_planning_model, org_id=None,
            task_id=self.owner.task_id, timeout=timeout, cancel_token=self.owner.cancellation_event,
            budget=self.owner.execution_budget))
        content = ""
        tokens = {"input_tokens":0,"output_tokens":0,"provider_credits":None}
        try:
            async for chunk in session.stream_chat(messages, reasoning_effort=self.settings.ecom_image_planning_reasoning):
                if chunk.content:
                    content += chunk.content
                tokens["input_tokens"] += chunk.prompt_tokens or 0
                tokens["output_tokens"] += chunk.completion_tokens or 0
                if chunk.credits_consumed is not None:
                    tokens["provider_credits"] = chunk.credits_consumed
            if session.last_result and session.last_result.status != "completed":
                raise RuntimeError("ECOM_PLAN_MODEL_ATTEMPT_INCOMPLETE")
            if session.last_result:
                usage=session.last_result.usage
                tokens["input_tokens"] = max(tokens["input_tokens"],int(usage.get("prompt_tokens",0)))
                tokens["output_tokens"] = max(tokens["output_tokens"],int(usage.get("completion_tokens",0)))
                tokens["provider_credits"] = usage.get("api_credits",tokens["provider_credits"])
            # A completed empty response is invalid stage output. Preserve its
            # measured usage so the bounded stage validator can repair it.
            user_credits = max(1,math.ceil(tokens["input_tokens"]*float(self.settings.ecom_image_planning_input_credits_per_million)/1_000_000
                + tokens["output_tokens"]*float(self.settings.ecom_image_planning_output_credits_per_million)/1_000_000))
            usage={**tokens,"user_credits":user_credits,"provider":"kie","model":self.settings.ecom_image_planning_model}
            return content, usage
        except asyncio.CancelledError:
            await self._save_attempt(row,lease,stage,{"outcome":"cancelled_or_uncertain"},"uncertain",None,charge=False)
            raise
        except Exception as error:
            await self._save_attempt(row,lease,stage,{"error_type":type(error).__name__,
                "http_status":getattr(error,"status_code",None),
                "provider_error_code":getattr(error,"error_code",None)},"uncertain",None,charge=False)
            raise
        finally:
            await session.close()

    async def _save_attempt(self,row,lease,stage,usage,status,repair,charge=False):
        attempt={"stage":stage,"status":status,"usage":usage,"repair_round":repair}
        params={"p_plan_id":row["id"],"p_lease_token":lease,"p_stage":stage,
            "p_output":None,"p_attempt":attempt,"p_status":"planning",
            "p_credits":usage.get("user_credits",0) if charge else 0}
        await asyncio.to_thread(lambda:self.scope.rpc("save_ecom_image_plan_stage",params).execute())

    async def _call_store(self, row, lease, stage, output, status, final, usage=None):
        from psycopg.types.json import Jsonb
        amount=(usage or {}).get("user_credits",0)
        attempt={"stage":stage,"status":"completed","usage":usage,"repair_round":None} if usage else None
        params={"p_plan_id":row["id"],"p_lease_token":lease,"p_stage":stage,
            # Stage two is a JSON string, not raw Markdown parsed as JSONB.
            "p_output":Jsonb(output) if isinstance(output, str) else output,
            "p_attempt":attempt,"p_status":status,
            "p_items":final.get("images") if final else None,
            "p_reviews":final.get("review_records") if final else None,"p_credits":amount}
        await asyncio.to_thread(lambda:self.scope.rpc("save_ecom_image_plan_stage",params).execute())

    async def _save(self,row,lease,stage,output,status,final,usage=None):
        return await self._call_store(row,lease,stage,output,status,final,usage)

    async def _fail(self,plan_id,lease,stage,status,error=None):
        try:
            await asyncio.to_thread(lambda:self.scope.rpc("save_ecom_image_plan_stage",{
                "p_plan_id":plan_id,"p_lease_token":lease,"p_stage":stage,"p_output":None,
                "p_attempt":{"stage":stage,"status":status,
                    **({"error_type":type(error).__name__} if error is not None else {})},
                "p_status":status}).execute())
        except Exception:
            pass

    async def _result(self,plan_id,revision,status):
        row=await asyncio.to_thread(lambda:self.scope.table("ecom_image_plans").select("id,plan_revision,status,current_stage,items,stage_outputs")
            .eq("id",plan_id).single().execute().data)
        if row["status"] == "ready":
            images=[{"item_id":i["item_id"],"position":i["position"],"name":i["name"],"purpose":i["purpose"]} for i in row["items"]]
            payload={"kind":"ecom_plan","plan_id":plan_id,"revision":row["plan_revision"],
                "product_insight":row["stage_outputs"]["1"]["product"].get("name") or "商品分析已完成",
                "visual_strategy":row["stage_outputs"]["2"],"images":images,"status":"ready"}
            tool_result={"status":"ready","plan_id":plan_id,"revision":row["plan_revision"],
                "image_count":len(images),"images":images,
                "instruction":"按position顺序逐项调用generate_image，只传对应plan_source。"}
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
