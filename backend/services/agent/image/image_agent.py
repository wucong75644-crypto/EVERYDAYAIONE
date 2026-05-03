"""
电商图片 Agent — 单张图片生成器

每次调用只生成1张图片。多张由主Agent拆分后多次调用。
与 ChatGPT + DALL-E 模式一致：主Agent拆分，ImageAgent单张执行。

生成流程：校验 → 锁积分 → 构建提示词 → KIE生图 → 裁切 → 上传CDN → 返回
失败时返回 failed ImagePart + retry_context，前端显示失败占位符。

积分操作复用 CreditMixin（乐观锁+重试+原子退款），不重复实现。

设计文档：docs/document/TECH_电商图片Agent.md §7
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from loguru import logger

from config.kie_models import calculate_image_cost
from core.exceptions import InsufficientCreditsError
from services.adapters.factory import create_image_adapter
from services.agent.agent_result import AgentResult
from services.handlers.mixins.credit_mixin import CreditMixin

from .image_processor import detect_aspect_ratio, detect_dimensions
from .prompt_builder import PromptBuilder

# CDN 域名白名单（防 SSRF）
_ALLOWED_IMAGE_HOSTS = frozenset({"cdn.everydayai.com.cn", "img.everydayai.com.cn"})


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
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        self.message_id = message_id
        self._prompt_builder = PromptBuilder()

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

        # 2. 计算并锁定积分（复用 CreditMixin，含乐观锁+重试）
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
            # 3. 构建最终提示词（注入全局风格约束）
            final_prompt = self._prompt_builder.build_final_prompt(task, style_directive)

            # 4. 预处理（白底图去背景 — 当前直接返回原图，rembg 在后续迭代实现）
            ref_images = image_urls

            # 5. 调 KIE adapter 生成图片
            aspect_ratio = detect_aspect_ratio(task)
            adapter = create_image_adapter(model_id)
            try:
                result = await adapter.generate(
                    prompt=final_prompt,
                    image_urls=ref_images if ref_images else None,
                    size=aspect_ratio,
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
                    task, image_urls, platform, style_directive,
                )

            # 6. 确认扣费
            self._confirm_deduct(tx_id)

            # 7. 返回成功结果
            cdn_url = result.image_urls[0]
            width, height = detect_dimensions(task, platform)

            logger.info(
                f"ImageAgent success | user={self.user_id} | model={model_id} "
                f"| size={width}x{height} | task={task[:50]}"
            )

            return AgentResult(
                status="success",
                summary=f"已生成图片：{task[:30]}",
                source="image_agent",
                collected_files=[{
                    "type": "image",
                    "url": cdn_url,
                    "width": width,
                    "height": height,
                    "alt": task[:50],
                }],
                metadata={"platform": platform, "model": model_id},
            )

        except Exception as e:
            if tx_id:
                try:
                    self._refund_credits(tx_id)
                except Exception as refund_err:
                    logger.critical(
                        f"CREDIT_LOSS_RISK: ImageAgent refund failed | "
                        f"tx={tx_id} | error={refund_err}"
                    )
            logger.opt(exception=True).error(f"ImageAgent error | task={task[:50]}")
            return self._error_result(
                f"图片生成异常：{e}", task, image_urls, platform, style_directive,
            )

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _select_model(self, image_urls: list[str]) -> str:
        """根据是否有参考图选择 KIE 模型。"""
        from core.config import get_settings
        settings = get_settings()
        if image_urls:
            return settings.image_agent_kie_i2i_model
        return settings.image_agent_kie_model

    def _validate_input(self, task: str, image_urls: list[str]) -> str | None:
        """输入校验。返回错误信息或 None（通过）。"""
        if not task or not task.strip():
            return "提示词不能为空"
        if len(task) > 2000:
            return "提示词过长，请精简到 2000 字以内"
        for url in image_urls:
            host = urlparse(url).hostname or ""
            if host and host not in _ALLOWED_IMAGE_HOSTS:
                return f"不支持的图片来源: {host}"
        return None

    def _error_result(
        self, summary: str,
        task: str, image_urls: list[str], platform: str, style_directive: str,
    ) -> AgentResult:
        """构建失败结果（含 failed ImagePart + retry_context）。"""
        width, height = detect_dimensions(task, platform)
        return AgentResult(
            status="error",
            summary=summary,
            source="image_agent",
            error_message=summary,
            collected_files=[{
                "type": "image",
                "url": None,
                "width": width,
                "height": height,
                "alt": task[:50],
                "failed": True,
                "error": summary,
                "retry_context": {
                    "task": task,
                    "image_urls": image_urls,
                    "platform": platform,
                    "style_directive": style_directive,
                },
            }],
        )
