"""
电商图模式 API

- POST /ecom-image/enhance-prompt  提示词增强（AI写提示词）
- POST /ecom-image/retry           单张图片原位重试

设计文档：docs/document/TECH_电商图片Agent.md §4
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
    """提示词增强请求"""
    text: str = Field(..., max_length=2000, description="用户简短描述")
    image_urls: list[str] = Field(default_factory=list, description="上传的图片CDN URLs")
    platform: str = Field(default="taobao", description="目标平台")
    style: Optional[str] = Field(default=None, description="风格预设 key")
    conversation_id: str = Field(default="", description="会话ID（风格持久化用，新对话可为空）")


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
# 风格调整检测
# ============================================================

_SATISFACTION_PATTERNS = [
    "很好", "不错", "可以", "满意", "喜欢", "就这个",
    "挺好", "好的", "OK", "ok", "行", "没问题",
    "保持", "继续", "一样的", "和上次一样",
]

_ADJUST_KEYWORDS = [
    "换个风格", "换风格", "风格改成", "风格换成",
    "不要这个风格", "重新设计",
    "暖一点", "冷一点", "亮一点", "暗一点",
    "颜色换", "色调调整", "配色改",
    "更高级", "更简约", "更活泼", "更年轻",
    "更大气", "更精致", "更有质感",
    "换成国潮", "换成极简", "换成清新",
    "试试网感", "来个复古",
    "不太满意", "不太对", "差点意思",
    "有点不一样", "调整一下",
]


def _is_style_adjustment(text: str) -> bool:
    """检测用户是否要求调整风格。

    两层判断：先排除肯定句式，再匹配调整关键词。
    """
    if any(p in text for p in _SATISFACTION_PATTERNS):
        return False
    return any(kw in text for kw in _ADJUST_KEYWORDS)


# ============================================================
# 辅助函数
# ============================================================

_TASK_PATTERN = re.compile(r"(\d+)\.\s*(.+?)(?=\n\d+\.|$)", re.DOTALL)


def _parse_image_tasks(content: str) -> list[dict[str, Any]]:
    """从模型输出解析结构化图片任务（"1. xxx" "2. xxx" 格式）。

    解析失败时兜底为整段当1张图。
    """
    tasks: list[dict[str, Any]] = []
    for match in _TASK_PATTERN.finditer(content):
        desc = match.group(2).strip()
        img_type = "main"
        if "白底" in desc:
            img_type = "white_bg"
        elif "场景" in desc:
            img_type = "scene"
        elif "详情" in desc or "卖点" in desc:
            img_type = "detail"
        aspect = "3:4" if ("750×1000" in desc or "3:4" in desc) else "1:1"
        tasks.append({
            "index": int(match.group(1)),
            "type": img_type,
            "description": desc,
            "aspect_ratio": aspect,
        })
    if not tasks:
        tasks = [{"index": 1, "type": "main", "description": content.strip(), "aspect_ratio": "1:1"}]
    return tasks


def _extract_style_directive(content: str) -> str:
    """从模型输出中提取风格描述（取关键行，最多5行）。"""
    style_keywords = ("配色", "色调", "光线", "风格", "氛围", "暖", "冷", "色系")
    lines = content.split("\n")
    style_lines = [line for line in lines if any(kw in line for kw in style_keywords)]
    return "\n".join(style_lines[:5]) if style_lines else content[:200]


def _estimate_credits(image_count: int) -> dict[str, int]:
    """预估积分消耗。"""
    per_image = 8  # gpt-image-2 1K 约 6-10 积分，取均值
    return {
        "estimated_credits": per_image * image_count,
        "per_image_credits": per_image,
        "image_count": image_count,
    }


def _build_multimodal_content(text: str, image_urls: list[str]) -> list[dict[str, Any]]:
    """构建多模态 user content（文字 + 图片）。"""
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for url in image_urls:
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
    """AI提示词增强 — 轻量API，3-10秒返回。

    输入用户简短描述（+ 可选商品图），返回专业的电商图片提示词。
    含结构化拆分 images[]、风格持久化、费用预估。
    """
    from core.config import get_settings
    from services.agent.image.prompt_builder import PromptBuilder

    settings = get_settings()
    builder = PromptBuilder()

    # 1. 品类检测
    category = builder.detect_category(req.text)

    # 2. 平台检测（文本关键词覆盖默认值）
    platform = builder.detect_platform(req.text, req.platform)

    # 3. 风格管理：三模式自动切换（create/reuse/update）
    # conversation_id 为空时跳过风格管理（新对话还没创建 conversation 记录）
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

    if not existing_style:
        style_mode = "create"
    elif _is_style_adjustment(req.text):
        style_mode = "update"
    else:
        style_mode = "reuse"

    # 4. 组装四层 system prompt
    user_style = req.style or builder.resolve_style_from_matrix(category, platform)
    system_prompt = builder.build_system_prompt(category, platform, user_style)

    if style_mode == "update":
        system_prompt += f"\n\n当前风格：\n{existing_style}\n\n用户要求调整，请在此基础上修改。"
    elif style_mode == "reuse":
        system_prompt += f"\n\n必须严格延续以下风格：\n{existing_style}"

    # 5. 构建 user message
    user_prompt = builder.build_enhance_prompt(
        req.text, platform, has_images=bool(req.image_urls),
        num_images=len(req.image_urls) if req.image_urls else None,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    if req.image_urls:
        messages.append({"role": "user", "content": _build_multimodal_content(user_prompt, req.image_urls)})
    else:
        messages.append({"role": "user", "content": user_prompt})

    # 6. 调 DashScope（有图片用 VL 模型，纯文字用普通模型）
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter

    model = settings.image_enhance_vl_model if req.image_urls else settings.image_enhance_model
    timeout = settings.image_enhance_timeout
    # 调用 LLM（主模型 → 降级备选，确保 adapter 资源关闭）
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

    # 主模型失败 → 降级到 fallback 模型
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
            return {"error": "提示词生成失败，请稍后重试", "success": False}
        finally:
            await adapter_fb.close()

    # 7. 解析结构化图片任务
    images = _parse_image_tasks(response.content)

    # 8. 提取并持久化 style_directive（conversation_id 为空时跳过写入）
    new_style = existing_style
    if style_mode in ("create", "update"):
        new_style = _extract_style_directive(response.content)
        if req.conversation_id and req.conversation_id.strip():
            try:
                db.table("conversations").update(
                    {"image_style_directive": new_style}
                ).eq("id", req.conversation_id).eq("user_id", user_id).execute()
            except Exception as e:
                logger.warning(f"持久化 style_directive 失败: {e}")

    logger.info(
        f"enhance-prompt | user={user_id} | category={category} | platform={platform} "
        f"| style_mode={style_mode} | images={len(images)} | model={model}"
    )

    return {
        "enhanced_prompt": response.content,
        "images": images,
        "style_directive": new_style or "",
        "style_mode": style_mode,
        "category": category,
        "platform": platform,
        "cost_estimate": _estimate_credits(len(images)),
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
    # 归属校验：conversation 和 message 必须属于当前用户
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
        # 原位替换消息中对应位置的 ImagePart
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
