"""Layer 3: 用户层 —— 最终发送给 LLM 的 user message。

职责:
  - 拼接附件 XML (workspace files + 当轮 attachments)
  - 拼接 user 原话 (不加时间戳前缀)
  - 当前附件身份与 user 原话在同一消息中，原话 text part 不改写
  - 处理多模态 (image_urls / file_urls 转 content list)
  - 支持 messages_attachments_as_system 配置 (向后兼容)

返回单条 user message dict (OpenAI/Anthropic 兼容)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional


def visual_measurement_guidance(text: str, has_images: bool) -> str:
    if not has_images or not re.search(
            r'几倍|多少(?:条|个|像素)|(?:宽度|长度|面积|尺寸|比例|占比).{0,16}(?:多少|几|多大|多长|多宽)', text):
        return ''
    return (
        '视觉问答口径提示（不是测量结果）：结合当前问题、图片和上下文区分数量、宽度或面积。'
        '条纹交替不代表等宽；用户要求目测时可给明确标注的估计，不冒充精确测量。'
        '只有确实无法确定比较对象或口径时才简短澄清。'
        '精确倍数须有可复查依据，不把缩略图或模型推测当作原图测量。'
    )


def visual_fact_guidance(text: str, has_images: bool) -> str:
    if (not has_images or not re.search(r'卖点|材质|面料|填充|功能|性能|容量|品牌', text)
            or re.search(r'(?:写|编写|创作|设计|策划|生成).{0,20}(?:方案|文案|提示词|图片)', text)):
        return ''
    return ('商品图片事实提示：现有商品的卖点分析以可见外观和用户资料为依据。'
            '图片不能证实材质成分、填充物、实际容量、手感、耐用、防尘防丢性能或品牌/IP授权；'
            '不把这些推测写成已确认事实。可见纹理与真实成分分开，文案建议中的性能卖点须注明待资料确认。')


@dataclass
class UserMessageInput:
    """用户层输入。"""

    text: str                                       # user 原话, 不含时间戳前缀
    workspace_files: List[Dict[str, Any]] = field(default_factory=list)
    attachments_xml: str = ""                       # 由 PromptBuilder 用 format_attachments 渲染好
    workspace_prompt: str = ""                      # 由 PromptBuilder 用 build_workspace_prompt 渲染好
    image_urls: List[str] = field(default_factory=list)
    image_sources: List[Dict[str, Any]] = field(default_factory=list)
    file_urls: List[str] = field(default_factory=list)
    attachments_as_system: bool = True              # True=独立 system block, False=附加到 user text
    org_id: Optional[str] = None


@dataclass
class UserLayerResult:
    """用户层渲染结果。"""

    user_message: Dict[str, Any]                    # 最终的 user message
    attachments_system_block: Optional[str] = None  # 如果 attachments_as_system=True, 单独返回 XML
    workspace_system_block: Optional[str] = None    # 如果有 workspace 文件清单, 单独返回


class UserLayer:
    """Layer 3 渲染器。"""

    @staticmethod
    def render(inp: UserMessageInput) -> UserLayerResult:
        # 决定 user text 内容
        if inp.attachments_as_system:
            user_text = inp.text
        else:
            user_text = inp.text + (inp.attachments_xml or "")

        # Keep attachment identity in the same turn as the user's request.
        # The full XML/action rules remain in their original system block.
        attachment_refs = ""
        if inp.attachments_as_system and inp.workspace_files:
            from services.handlers.chat_context.attachments import format_current_attachment_refs
            attachment_refs = format_current_attachment_refs(inp.workspace_files, inp.org_id)

        from services.handlers.chat_context.image_sources import format_image_sources
        # Only real image metadata belongs here. Output intent is interpreted by
        # the existing workflow once, then checked by native input preparation.
        sources = format_image_sources(inp.image_sources)
        if sources:
            attachment_refs = "\n\n".join(filter(None, (attachment_refs, sources)))
        has_images = bool(inp.image_urls or inp.image_sources)
        measurement = visual_measurement_guidance(inp.text, has_images) + visual_fact_guidance(inp.text, has_images)
        if measurement:
            attachment_refs = '\n\n'.join(filter(None, (attachment_refs, measurement)))

        # 构造 user message (多模态判断)
        if attachment_refs or inp.image_urls or inp.file_urls:
            media_parts = [
                {"type": "image_url", "image_url": {"url": u}}
                for u in (inp.image_urls + inp.file_urls)
            ]
            content = [{"type": "text", "text": user_text}]
            if attachment_refs:
                content.append({"type": "text", "text": attachment_refs})
            content.extend(media_parts)
        else:
            content = user_text

        user_msg = {"role": "user", "content": content}

        # 决定附件 system block 是否单独返回
        attach_block = None
        if inp.attachments_as_system and inp.attachments_xml.strip():
            attach_block = inp.attachments_xml.strip()

        workspace_block = None
        if inp.workspace_prompt and inp.workspace_prompt.strip():
            workspace_block = inp.workspace_prompt.strip()

        return UserLayerResult(
            user_message=user_msg,
            attachments_system_block=attach_block,
            workspace_system_block=workspace_block,
        )
