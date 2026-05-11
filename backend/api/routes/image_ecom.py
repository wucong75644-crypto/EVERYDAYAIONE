"""
电商图模式 API（v2）

- POST /ecom-image/enhance-prompt  方案策划（千问VL一步到位输出gpt-image-2 prompt）
- POST /ecom-image/retry           单张图片原位重试

设计文档：docs/document/TECH_电商图片Agent_v2.md
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database

router = APIRouter(prefix="/ecom-image", tags=["ecom-image"])


# ============================================================
# Request / Response 模型
# ============================================================

class EnhancePromptRequest(BaseModel):
    """方案策划请求（v2）"""
    # 必填
    product_name: str = Field(default="", max_length=100, description="产品名称")
    image_urls: list[str] = Field(default_factory=list, description="产品图 CDN URLs")
    platform: str = Field(default="taobao", description="目标平台")
    # 选填
    style_ref_urls: list[str] = Field(default_factory=list, description="风格参考图 CDN URLs")
    selling_points: str = Field(default="", max_length=500, description="核心卖点")
    price_info: str = Field(default="", max_length=200, description="价格/促销信息")
    target_user: str = Field(default="", max_length=200, description="目标用户")
    extra_notes: str = Field(default="", max_length=500, description="补充说明")
    # 生成设置
    image_size: str = Field(default="800x800", description="图片尺寸")
    generate_detail: bool = Field(default=False, description="是否生成详情页")
    # 向后兼容 v1
    text: str = Field(default="", max_length=2000, description="v1 兼容：用户简短描述")
    conversation_id: str = Field(default="", description="会话ID（风格持久化）")


class RetryImageRequest(BaseModel):
    """单张图片重试请求"""
    conversation_id: str
    message_id: str
    task: str = Field(..., description="原完整提示词")
    image_urls: list[str] = Field(default_factory=list, description="原上传图片")
    platform: str = Field(default="taobao")
    style_directive: str = Field(default="", description="原风格约束")
    part_index: int = Field(default=0, description="消息中第几张图")


# ============================================================
# 辅助函数
# ============================================================

def _parse_design_plan(content: str) -> dict[str, Any]:
    """解析千问输出的设计方案 JSON。

    三层兜底：直接解析 → 正则提取 JSON → 整段作为单张 prompt。
    """
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 兜底：提取最大的 JSON 块（千问可能在 JSON 前后加解释文字）
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 最终兜底：返回解析失败标记，前端据此提示用户重试
    logger.warning(f"Failed to parse design plan JSON, using fallback | len={len(content)}")
    return {
        "product_insight": "",
        "visual_strategy": "",
        "images": [],
        "_parse_failed": True,
    }


def sync_text_to_prompt(prompt: str, new_title: str, new_subtitle: str) -> str:
    """将用户编辑的文案同步回 prompt 中的引号标记位置。

    prompt 中中文文案用引号包裹（如 "一盒搞定"），
    按顺序替换：第一个引号对替换为 title，第二个替换为 subtitle。

    注意：前端 EcomPlanCards.syncTextToPrompt 已实时同步（用户编辑即时更新 prompt），
    本函数作为后端兜底——当前未被调用，保留用于后续 retry 或服务端文案覆盖场景。
    """
    # 匹配中文引号内容（至少包含一个中文字符的引号对）
    pattern = r'"([^"]*[\u4e00-\u9fff][^"]*)"'
    matches = list(re.finditer(pattern, prompt))

    if not matches:
        return prompt

    result = prompt
    # 从后往前替换，避免偏移量问题
    if len(matches) >= 2 and new_subtitle:
        m = matches[1]
        result = result[:m.start()] + f'"{new_subtitle}"' + result[m.end():]
    if matches and new_title:
        m = matches[0]
        result = result[:m.start()] + f'"{new_title}"' + result[m.end():]

    return result


def _estimate_credits(image_count: int) -> dict[str, int]:
    """预估积分消耗。"""
    per_image = 8  # gpt-image-2 1K 约 6-10 积分，取均值
    return {
        "estimated_credits": per_image * image_count,
        "per_image_credits": per_image,
        "image_count": image_count,
    }


def _build_multimodal_content(
    text: str,
    product_urls: list[str],
    style_ref_urls: list[str],
) -> list[dict[str, Any]]:
    """构建多模态 user content（文字 + 产品图 + 风格参考图）。"""
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for url in product_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    for url in style_ref_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


# ============================================================
# POST /ecom-image/enhance-prompt
# ============================================================

@router.post("/enhance-prompt")
async def enhance_prompt(
    req: EnhancePromptRequest,
    user_id: CurrentUserId,
    db: Database,
) -> dict[str, Any]:
    """方案策划 API（v2）— 千问VL一步到位。

    输入产品信息+图片 → 千问VL理解产品+策划方案+输出gpt-image-2 prompt
    → 返回结构化 JSON（images[]含每张图的prompt）。
    """
    from core.config import get_settings
    from services.agent.image.prompt_builder import PromptBuilder

    settings = get_settings()
    builder = PromptBuilder()

    # v1 兼容：如果 product_name 为空但 text 有值，用 text 作为 product_name
    product_name = req.product_name or req.text or ""
    platform = req.platform

    # 0. 信息充足判断：至少需要产品图 + 产品描述
    missing = []
    if not req.image_urls:
        missing.append("产品图片（请上传至少1张产品照片）")
    if not product_name.strip() or product_name.strip() == "产品":
        missing.append("产品描述（请告诉我这是什么产品）")

    if missing:
        guide = "我需要以下信息来为你策划电商主图方案：\n\n"
        for i, item in enumerate(missing, 1):
            guide += f"{i}. {item}\n"
        guide += "\n💡 示例：上传产品图后输入「221色拼豆收纳盒 淘宝5张主图 核心卖点大容量分类收纳」"
        return {
            "guide_message": guide,
            "images": [],
            "product_insight": "",
            "visual_strategy": "",
            "cost_estimate": None,
            "platform": platform,
            "enhanced_prompt": "",
            "style_directive": "",
        }

    # 1. 风格管理：读取已有风格（用于多轮对话风格延续）
    existing_style: str | None = None
    if req.conversation_id and req.conversation_id.strip():
        try:
            row = db.table("conversations").select("image_style_directive").eq(
                "id", req.conversation_id,
            ).eq("user_id", user_id).maybe_single().execute()
            if row and row.data:
                existing_style = row.data.get("image_style_directive")
        except Exception as e:
            logger.warning(f"读取 style_directive 失败: {e}")

    # 2. 组装三层 system prompt
    system_prompt = builder.build_system_prompt(platform)
    if existing_style:
        system_prompt += (
            f"\n\n## 风格延续\n"
            f"上一轮的视觉策略：{existing_style}\n"
            f"请在此基础上保持风格一致性，除非用户明确要求调整。"
        )

    # 3. 组装 user message
    user_prompt = builder.build_user_message(
        product_name=product_name,
        platform=platform,
        product_image_count=len(req.image_urls),
        style_ref_count=len(req.style_ref_urls),
        selling_points=req.selling_points,
        price_info=req.price_info,
        target_user=req.target_user,
        extra_notes=req.extra_notes,
        image_size=req.image_size,
        generate_detail=req.generate_detail,
    )

    # 4. 构建多模态消息
    all_image_urls = req.image_urls + req.style_ref_urls
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    if all_image_urls:
        messages.append({
            "role": "user",
            "content": _build_multimodal_content(user_prompt, req.image_urls, req.style_ref_urls),
        })
    else:
        messages.append({"role": "user", "content": user_prompt})

    # 5. 调千问 VL（主模型 → 降级备选）
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter

    model = settings.image_enhance_vl_model if all_image_urls else settings.image_enhance_model
    timeout = settings.image_enhance_timeout

    response = None
    adapter = DashScopeChatAdapter(
        api_key=settings.dashscope_api_key or "",
        model=model,
        base_url=settings.dashscope_base_url,
        stream_timeout=timeout,
    )
    try:
        response = await adapter.chat_sync(messages=messages)
    except Exception as primary_err:
        logger.warning(f"enhance primary model failed: {primary_err}, trying fallback")
    finally:
        await adapter.close()

    if response is None:
        fallback = settings.image_enhance_fallback_model
        adapter_fb = DashScopeChatAdapter(
            api_key=settings.dashscope_api_key or "",
            model=fallback,
            base_url=settings.dashscope_base_url,
            stream_timeout=timeout,
        )
        try:
            response = await adapter_fb.chat_sync(messages=messages)
            model = fallback
        except Exception as fallback_err:
            logger.error(f"enhance fallback also failed: {fallback_err}")
            return {"error": "方案生成失败，请稍后重试", "success": False}
        finally:
            await adapter_fb.close()

    # 6. 解析设计方案 JSON
    plan = _parse_design_plan(response.content)
    images = plan.get("images", [])

    # 7. 持久化 visual_strategy 作为 style_directive
    new_style = plan.get("visual_strategy", "") or existing_style
    if new_style and req.conversation_id and req.conversation_id.strip():
        try:
            db.table("conversations").update(
                {"image_style_directive": new_style}
            ).eq("id", req.conversation_id).eq("user_id", user_id).execute()
        except Exception as e:
            logger.warning(f"持久化 style_directive 失败: {e}")

    logger.info(
        f"enhance-prompt v2 | user={user_id} | platform={platform} "
        f"| product={product_name[:20]} | images={len(images)} | model={model}"
    )

    return {
        "product_insight": plan.get("product_insight", ""),
        "visual_strategy": plan.get("visual_strategy", ""),
        "images": images,
        "style_directive": new_style or "",
        "platform": platform,
        "cost_estimate": _estimate_credits(len(images)),
        # v1 兼容字段
        "enhanced_prompt": response.content,
    }


# ============================================================
# POST /ecom-image/retry
# ============================================================

@router.post("/retry")
async def retry_image(
    req: RetryImageRequest,
    user_id: CurrentUserId,
    db: Database,
) -> dict[str, Any]:
    """单张图片原位重新生成。

    复用 retry_context 中的完整提示词+图片+风格，
    成功后原位替换消息中对应位置的 ImagePart。
    """
    conv_check = db.table("conversations").select("id").eq(
        "id", req.conversation_id,
    ).eq("user_id", user_id).maybe_single().execute()
    if not conv_check or not conv_check.data:
        return {"success": False, "error": "对话不存在或无权访问"}

    from services.agent.image.image_agent import ImageAgent

    agent = ImageAgent(
        db=db,
        user_id=user_id,
        conversation_id=req.conversation_id,
    )
    result = await agent.execute(
        task=req.task,
        image_urls=req.image_urls,
        platform=req.platform,
        style_directive=req.style_directive,
    )

    if result.status == "success" and result.collected_files:
        try:
            _update_message_image_part(
                db, req.message_id, req.part_index, result.collected_files[0],
            )
        except Exception as e:
            logger.error(f"retry update message failed: {e}")
        return {"success": True, "image_url": result.collected_files[0].get("url", "")}

    return {"success": False, "error": result.summary}


def _update_message_image_part(
    db: Any, message_id: str, part_index: int, new_part: dict,
) -> None:
    """更新消息 content 中指定位置的 ImagePart（原位替换）。"""
    row = db.table("messages").select("content").eq("id", message_id).single().execute()
    content: list = row.data.get("content") or []

    img_count = 0
    for i, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "image":
            if img_count == part_index:
                content[i] = new_part
                break
            img_count += 1

    db.table("messages").update(
        {"content": json.dumps(content, ensure_ascii=False)}
    ).eq("id", message_id).execute()
