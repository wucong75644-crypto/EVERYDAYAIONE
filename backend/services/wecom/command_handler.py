"""
企微文本指令处理器

识别用户发送的文本指令（如"查积分""帮助"），回复卡片或文本。
指令作为卡片交互的备用入口——用户不点按钮也能通过打字触发功能。

匹配规则：完全匹配或 ^ 开头匹配，不拦截含问号/感叹号的句子。
"""

import re
from typing import List, Optional, Tuple

from loguru import logger


from core.config import get_settings
from schemas.wecom import WecomReplyContext
from services.wecom.card_builder import WECOM_MODEL_OPTIONS, WecomCardBuilder

# ── 指令定义 ──────────────────────────────────────────

# (正则, handler_name)
_COMMAND_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^(帮助|help|指令|命令|功能)$"), "help"),
    (re.compile(r"^(查积分|我的积分|积分余额|余额|积分)$"), "credits"),
    (re.compile(r"^(我的记忆|查看记忆|记忆列表)$"), "memory"),
    (re.compile(r"^(清空记忆|删除所有记忆)$"), "clear_memory"),
    (re.compile(r"^(新对话|新建对话|开始新对话)$"), "new_conversation"),
    (re.compile(r"^(深度思考|快速回复|思考模式)$"), "thinking"),
    (re.compile(r"^(切换模型|选模型)$"), "switch_model"),
    # "用xxx" / "换xxx" / "切换到xxx" → 直接切换
    (re.compile(r"^(?:用|切换到?|使用|换)\s*(.+)$"), "switch_model_direct"),
]


class CommandHandler:
    """企微文本指令识别 + 卡片/文本回复"""

    def __init__(self, db):
        self.db = db
        self._settings = get_settings()

    async def try_handle(
        self,
        text: str,
        user_id: str,
        conversation_id: str,
        reply_ctx: WecomReplyContext,
    ) -> bool:
        """尝试匹配并处理指令。

        Returns:
            True = 已处理（不进入 AI 路由）
            False = 非指令，继续正常流程
        """
        stripped = text.strip()
        if not stripped:
            return False

        # 含问号/感叹号的句子不拦截（如"帮助我做xxx"）
        if len(stripped) > 10:
            return False

        for pattern, cmd_name in _COMMAND_PATTERNS:
            match = pattern.match(stripped)
            if match:
                logger.info(
                    f"Command matched | cmd={cmd_name} | text={stripped} | "
                    f"user_id={user_id}"
                )
                await self._dispatch(
                    cmd_name, match, user_id, conversation_id, reply_ctx
                )
                return True

        return False

    async def _dispatch(
        self,
        cmd_name: str,
        match: re.Match,
        user_id: str,
        conversation_id: str,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """分发到对应处理逻辑"""
        ws = reply_ctx.ws_client
        if not ws or not reply_ctx.req_id:
            return

        if cmd_name == "help":
            await ws.send_template_card(
                reply_ctx.req_id, WecomCardBuilder.help_card()
            )

        elif cmd_name == "credits":
            from services.credit_service import CreditService
            credit_svc = CreditService(self.db, redis=None)
            balance = await credit_svc.get_balance(user_id)
            await ws.send_template_card(
                reply_ctx.req_id, WecomCardBuilder.credits_card(balance)
            )

        elif cmd_name == "memory":
            from services.memory_service import MemoryService
            mem_svc = MemoryService()
            memories = await mem_svc.get_all_memories(user_id)
            if memories:
                card = WecomCardBuilder.memory_list_card(memories)
            else:
                card = WecomCardBuilder.memory_empty_card()
            await ws.send_template_card(reply_ctx.req_id, card)

        elif cmd_name == "clear_memory":
            from services.memory_service import MemoryService
            mem_svc = MemoryService()
            await mem_svc.delete_all_memories(user_id)
            await ws.send_reply(
                reply_ctx.req_id, "text", {"content": "已清空所有记忆"}
            )

        elif cmd_name == "new_conversation":
            from services.conversation_service import ConversationService
            conv_svc = ConversationService(self.db)
            await conv_svc.create_conversation(
                user_id, title="企微对话", model_id="auto"
            )
            await ws.send_template_card(
                reply_ctx.req_id, WecomCardBuilder.new_conversation_card()
            )

        elif cmd_name == "thinking":
            from services.wecom.wecom_message_service import WecomMessageService
            current = WecomMessageService.get_session_setting(
                conversation_id, "thinking_mode"
            )
            await ws.send_template_card(
                reply_ctx.req_id,
                WecomCardBuilder.thinking_mode_card(current or "fast"),
            )

        elif cmd_name == "switch_model":
            from services.wecom.wecom_message_service import WecomMessageService
            current = WecomMessageService.get_session_setting(
                conversation_id, "model"
            )
            await ws.send_template_card(
                reply_ctx.req_id,
                WecomCardBuilder.model_select_card(current_model=current),
            )

        elif cmd_name == "switch_model_direct":
            await self._switch_model_direct(
                match.group(1).strip(), user_id, conversation_id, reply_ctx
            )

    async def _switch_model_direct(
        self,
        model_text: str,
        user_id: str,
        conversation_id: str,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """直接切换模型（"用deepseek""换gemini"）"""
        model_id = self._fuzzy_match_model(model_text)
        ws = reply_ctx.ws_client
        if not ws or not reply_ctx.req_id:
            return

        if not model_id:
            await ws.send_reply(
                reply_ctx.req_id, "text",
                {"content": f"没找到模型「{model_text}」，试试发「切换模型」查看可用列表"},
            )
            return

        from services.wecom.wecom_message_service import WecomMessageService
        WecomMessageService.set_session_setting(conversation_id, "model", model_id)

        model_name = model_id
        for m in WECOM_MODEL_OPTIONS:
            if m["id"] == model_id:
                model_name = m["text"]
                break

        await ws.send_template_card(
            reply_ctx.req_id,
            WecomCardBuilder.model_switched_card(model_name),
        )

    @staticmethod
    def _fuzzy_match_model(text: str) -> Optional[str]:
        """模糊匹配模型名（不区分大小写，支持部分匹配）"""
        text_lower = text.lower().strip()
        for m in WECOM_MODEL_OPTIONS:
            mid = m["id"].lower()
            mtext = m["text"].lower()
            if text_lower == mid or text_lower == mtext:
                return m["id"]
            if text_lower in mid or text_lower in mtext:
                return m["id"]
        return None
