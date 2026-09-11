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
from typing import Any, Dict, List, Optional


@dataclass
class UserMessageInput:
    """用户层输入。"""

    text: str                                       # user 原话, 不含时间戳前缀
    workspace_files: List[Dict[str, Any]] = field(default_factory=list)
    attachments_xml: str = ""                       # 由 PromptBuilder 用 format_attachments 渲染好
    workspace_prompt: str = ""                      # 由 PromptBuilder 用 build_workspace_prompt 渲染好
    image_urls: List[str] = field(default_factory=list)
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
