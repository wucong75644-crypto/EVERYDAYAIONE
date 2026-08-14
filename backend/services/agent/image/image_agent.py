"""
电商图片 Agent（v2）

两阶段执行：
  Phase 1 — ecom_plan()：调千问VL分析产品+策划方案，返回 ecom_plan 方案卡片
  Phase 2 — execute()：拿方案中的 prompt 逐张调 gpt-image-2 生图

也兼容单张生图模式（主Agent工具调用 / 单张重试）。

设计文档：docs/document/TECH_电商图片Agent_v2.md
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from config.kie_models import calculate_image_cost
from core.exceptions import InsufficientCreditsError
from services.adapters.factory import create_image_adapter
from services.agent.agent_result import AgentResult
from services.agent.safe_tool_logging import log_agent_event
from services.agent.runtime.ecom_capability import get_runtime_ecom_capability
from services.handlers.mixins.credit_mixin import CreditMixin
from .image_agent_helpers import (
    build_image_error_result,
    parse_image_plan,
    validate_image_request,
)
from .image_processor import detect_aspect_ratio, detect_dimensions
from .prompt_builder import PromptBuilder


class ImageAgent(CreditMixin):
    """电商图片生成 Agent — 单张图片生成器。

    继承 CreditMixin 复用积分 lock/confirm/refund（含乐观锁+重试+原子退款）。
    宿主属性：self.db, self.user_id, self.org_id（CreditMixin 依赖）。
    """

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
        task_id: str | None = None,
        message_id: str | None = None,
        workspace_user_id: str | None = None,
        runtime_owned: bool = True,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.workspace_user_id = workspace_user_id or user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        self.message_id = message_id
        self.runtime_owned = runtime_owned
        self._prompt_builder = PromptBuilder()

    # ----------------------------------------------------------
    # Phase 1：方案策划（调千问VL，返回 ecom_plan 卡片）
    # ----------------------------------------------------------

    async def ecom_plan(
        self,
        user_text: str,
        image_urls: list[str] | None = None,
        platform: str = "taobao",
    ) -> AgentResult:
        """分析产品并策划电商主图方案。

        调千问VL一步到位输出 gpt-image-2 可执行的 prompt JSON。
        返回 AgentResult,emit_payloads 中放 ecom_plan 数据(前端渲染为方案卡片)。

        Args:
            user_text: 用户输入的产品描述
            image_urls: 用户上传的产品图 CDN URLs
            platform: 目标平台

        Returns:
            AgentResult(status="plan"): 方案数据在 metadata["ecom_plan"] 中
            AgentResult(status="error"): 信息不足或生成失败
        """
        from core.config import get_settings

        product_name = user_text.strip()
        validation_error = self._validate_plan_request(
            product_name, image_urls,
        )
        if validation_error:
            return validation_error
        settings = get_settings()
        messages = self._build_plan_messages(
            product_name, platform, image_urls or [],
        )
        response, model, request_error = await self._request_plan(
            settings, messages, bool(image_urls),
        )
        if request_error:
            return request_error
        assert response is not None

        # 解析JSON
        plan = self._parse_plan_json(response.content)

        images = plan.get("images", [])
        if not images:
            return AgentResult(
                status="error",
                summary="方案生成失败（AI返回格式异常），请重试",
                source="image_agent",
                error_message="JSON解析后images为空",
            )

        # 持久化 visual_strategy
        new_style = plan.get("visual_strategy", "")
        if new_style:
            self._save_style_directive(new_style)

        log_agent_event("info", "ecom_plan success", self, "image_agent")

        # 返回方案（status="plan" 表示等待用户确认）
        return AgentResult(
            status="plan",
            summary=f"已为「{product_name}」策划了 {len(images)} 张主图方案，请确认后生成。",
            source="image_agent",
            metadata={
                "ecom_plan": {
                    "product_insight": plan.get("product_insight", ""),
                    "visual_strategy": plan.get("visual_strategy", ""),
                    "images": images,
                    "cost_estimate": {
                        "estimated_credits": 8 * len(images),
                        "image_count": len(images),
                    },
                },
            },
        )

    def _validate_plan_request(
        self,
        product_name: str,
        image_urls: list[str] | None,
    ) -> AgentResult | None:
        missing = []
        if not image_urls:
            missing.append("产品图片（请上传至少1张产品照片）")
        if not product_name:
            missing.append("产品描述（请告诉我这是什么产品）")
        if not missing:
            return None
        items = "\n".join(
            f"{index}. {item}"
            for index, item in enumerate(missing, 1)
        )
        guide = (
            f"我需要以下信息来为你策划电商主图方案：\n\n{items}\n\n"
            "💡 示例：上传产品图后输入「221色拼豆收纳盒 淘宝5张主图 "
            "核心卖点大容量分类收纳」"
        )
        return AgentResult(
            status="error",
            summary=guide,
            source="image_agent",
            error_message=guide,
        )

    def _build_plan_messages(
        self,
        product_name: str,
        platform: str,
        image_urls: list[str],
    ) -> list[dict[str, Any]]:
        system_prompt = self._prompt_builder.build_system_prompt(platform)
        existing_style = self._read_style_directive()
        if existing_style:
            system_prompt += (
                "\n\n## 风格延续\n"
                f"上一轮的视觉策略：{existing_style}\n"
                "请在此基础上保持风格一致性，除非用户明确要求调整。"
            )
        user_prompt = self._prompt_builder.build_user_message(
            product_name=product_name,
            platform=platform,
            product_image_count=len(image_urls),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if not image_urls:
            messages.append({"role": "user", "content": user_prompt})
            return messages
        content: list[dict[str, Any]] = [
            {"type": "text", "text": user_prompt},
        ]
        content.extend(
            {"type": "image_url", "image_url": {"url": url}}
            for url in image_urls
        )
        messages.append({"role": "user", "content": content})
        return messages

    async def _request_plan(
        self,
        settings: Any,
        messages: list[dict[str, Any]],
        has_images: bool,
    ) -> tuple[Any | None, str, AgentResult | None]:
        model = (
            settings.image_enhance_vl_model
            if has_images else settings.image_enhance_model
        )
        if not self.runtime_owned:
            from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter

            models = [model, settings.image_enhance_fallback_model]
            last_error: Exception | None = None
            for index, candidate in enumerate(models):
                adapter = DashScopeChatAdapter(
                    api_key=settings.dashscope_api_key or "",
                    model=candidate,
                    base_url=settings.dashscope_base_url,
                    stream_timeout=settings.image_enhance_timeout,
                )
                try:
                    return await adapter.chat_sync(messages=messages), candidate, None
                except Exception as error:
                    last_error = error
                    log_agent_event(
                        "warning" if index == 0 else "error", "ecom_plan model failed",
                        self, "image_agent", "IMAGE_PLAN_MODEL_FAILED", type(error).__name__,
                    )
                finally:
                    await adapter.close()
            return None, models[-1], AgentResult(
                status="error", summary="方案生成失败，请稍后重试",
                source="image_agent", error_message=f"LLM调用失败: {last_error}",
            )
        try:
            return (
                await get_runtime_ecom_capability().invoke_model(
                    messages=messages, model=model,
                    timeout_seconds=settings.image_enhance_timeout,
                    org_id=self.org_id,
                ),
                model,
                None,
            )
        except RuntimeEcomCapabilityUnavailable as error:
            log_agent_event(
                "warning", "ecom_plan blocked by Runtime capability",
                self, "image_agent", "RUNTIME_ECOM_MODEL_UNAVAILABLE",
                type(error).__name__,
            )
        error_result = AgentResult(
            status="error",
            summary="方案生成服务暂未接入 Runtime，请稍后重试",
            source="image_agent",
            error_message="RUNTIME_ECOM_MODEL_INGRESS_UNAVAILABLE",
        )
        return None, model, error_result

    # ----------------------------------------------------------
    # Phase 2：单张图片生成（原 execute，保持不变）
    # ----------------------------------------------------------

    async def execute(self, task: str, **kwargs: Any) -> AgentResult:
        """生成单张图片。

        Args:
            task: 单张图片的生成描述（主Agent从增强提示词中拆分出来的）
            kwargs:
                image_urls: list[str]       — 用户上传的参考图CDN URLs
                platform: str               — 目标平台（决定裁切尺寸）
                style_directive: str        — 全局风格约束（executor 从DB自动注入）
                history_images: list[dict]  — 历史生成图片（供修改引用）

        Returns:
            AgentResult: 成功含图片URL，失败含 retry_context
        """
        image_urls: list[str] = kwargs.get("image_urls", [])
        platform: str = kwargs.get("platform", "taobao")
        style_directive: str = kwargs.get("style_directive", "")

        # 1. 校验
        err = self._validate_input(task, image_urls)
        if err:
            return AgentResult(
                status="error", summary=err, source="image_agent",
                error_message=err,
            )

        # 2. Runtime capability gate 必须先于积分锁定，避免 blocked 请求产生账务副作用。
        capability = get_runtime_ecom_capability()
        if self.runtime_owned and not getattr(capability, "ready", False):
            return self._error_result(
                "图片生成服务暂未接入 Runtime，请稍后重试",
                task, image_urls, platform, style_directive,
            )

        # 3. 计算并锁定积分（复用 CreditMixin，含乐观锁+重试）
        model_id = self._select_model(image_urls)
        try:
            cost = calculate_image_cost(model_name=model_id, image_count=1)
            credits_needed = cost["user_credits"]
        except Exception as e:
            return self._error_result(
                f"积分计算失败：{e}", task, image_urls, platform, style_directive,
            )

        lock_task_id = f"img_ecom_{uuid4().hex[:8]}"
        tx_id: str | None = None
        try:
            tx_id = self._lock_credits(
                task_id=lock_task_id,
                user_id=self.user_id,
                amount=credits_needed,
                reason=f"EcomImage: {task[:30]}",
                org_id=self.org_id,
            )
        except InsufficientCreditsError as e:
            # 积分不足：不返回 failed ImagePart（充值后手动重试）
            return AgentResult(
                status="error", summary=str(e), source="image_agent",
                error_message=str(e),
            )
        except Exception as e:
            return self._error_result(
                f"积分锁定失败：{e}", task, image_urls, platform, style_directive,
            )

        try:
            return await self._generate_ecom_image(
                task=task,
                image_urls=image_urls,
                platform=platform,
                style_directive=style_directive,
                model_id=model_id,
                tx_id=tx_id,
            )
        except Exception as e:
            if tx_id:
                try:
                    self._refund_credits(tx_id)
                except Exception as refund_err:
                    log_agent_event(
                        "critical", "ImageAgent refund failed", self, "image_agent",
                        "IMAGE_REFUND_FAILED", type(refund_err).__name__,
                    )
            log_agent_event(
                "error", "ImageAgent error", self, "image_agent",
                "IMAGE_GENERATION_FAILED", type(e).__name__,
            )
            return self._error_result(
                f"图片生成异常：{e}", task, image_urls, platform, style_directive,
            )

    async def _generate_ecom_image(
        self,
        task: str,
        image_urls: list[str],
        platform: str,
        style_directive: str,
        model_id: str,
        tx_id: str,
    ) -> AgentResult:
        final_prompt = self._prompt_builder.build_final_prompt(
            task, style_directive,
        )
        if self.runtime_owned:
            result = await get_runtime_ecom_capability().invoke_image(
                prompt=final_prompt,
                image_urls=image_urls or None,
                model=model_id,
                org_id=self.org_id,
                size=detect_aspect_ratio(task),
                wait_for_result=True,
                max_wait_time=90.0,
                poll_interval=2.0,
            )
        else:
            adapter = create_image_adapter(model_id)
            try:
                result = await adapter.generate(
                    prompt=final_prompt,
                    image_urls=image_urls or None,
                    size=detect_aspect_ratio(task),
                    wait_for_result=True,
                    max_wait_time=90.0,
                    poll_interval=2.0,
                )
            finally:
                await adapter.close()
        if not result.image_urls:
            self._refund_credits(tx_id)
            return self._error_result(
                f"图片生成失败：{result.fail_msg or '未知错误'}",
                task,
                image_urls,
                platform,
                style_directive,
            )
        self._confirm_deduct(tx_id)
        width, height = detect_dimensions(task, platform)
        log_agent_event("info", "ImageAgent success", self, "image_agent")
        from services.file_upload import persist_media_urls_to_workspace

        emit_payloads = await persist_media_urls_to_workspace(
            urls=result.image_urls,
            user_id=self.workspace_user_id,
            org_id=self.org_id,
            media_type="image",
            meta={
                "prompt": task,
                "model": model_id,
                "platform": platform,
                "style_directive": style_directive,
                "reference_images": image_urls,
                "width": width,
                "height": height,
            },
            extra_fields={
                "width": width,
                "height": height,
                "alt": task[:50],
            },
        )
        for payload in emit_payloads:
            payload.update({
                "_asset_source_kind": "ecom_image",
                "_asset_prompt": task,
                "_asset_model_id": model_id,
            })
        return AgentResult(
            status="success",
            summary=f"已生成图片：{task[:30]}",
            source="image_agent",
            emit_payloads=emit_payloads,
            metadata={"platform": platform, "model": model_id},
        )

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _parse_plan_json(self, content: str) -> dict[str, Any]:
        """解析千问输出的方案 JSON（三层兜底）。"""
        return parse_image_plan(content, self)

    def _read_style_directive(self) -> str:
        """从 DB 读取会话级风格。"""
        try:
            row = self.db.table("conversations").select(
                "image_style_directive"
            ).eq("id", self.conversation_id).maybe_single().execute()
            if row and row.data:
                return row.data.get("image_style_directive") or ""
        except Exception as e:
            log_agent_event(
                "warning", "ImageAgent style read failed", self, "image_agent",
                "IMAGE_STYLE_READ_FAILED", type(e).__name__,
            )
        return ""

    def _save_style_directive(self, style: str) -> None:
        """持久化视觉策略到 DB。"""
        try:
            self.db.table("conversations").update(
                {"image_style_directive": style}
            ).eq("id", self.conversation_id).execute()
        except Exception as e:
            log_agent_event(
                "warning", "ImageAgent style write failed", self, "image_agent",
                "IMAGE_STYLE_WRITE_FAILED", type(e).__name__,
            )

    def _select_model(self, image_urls: list[str]) -> str:
        """根据是否有参考图选择 KIE 模型。"""
        from core.config import get_settings
        settings = get_settings()
        if image_urls:
            return settings.image_agent_kie_i2i_model
        return settings.image_agent_kie_model

    def _validate_input(self, task: str, image_urls: list[str]) -> str | None:
        """输入校验。返回错误信息或 None（通过）。"""
        return validate_image_request(task, image_urls)

    def _error_result(
        self, summary: str,
        task: str, image_urls: list[str], platform: str, style_directive: str,
    ) -> AgentResult:
        """构建失败结果（含 failed ImagePart + retry_context）。"""
        return build_image_error_result(
            summary, task, image_urls, platform, style_directive,
        )
