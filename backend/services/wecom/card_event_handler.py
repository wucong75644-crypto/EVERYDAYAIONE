"""
企微模板卡片事件处理器

处理用户点击卡片按钮 / 提交选择后的回调。
必须在 5 秒内回复更新卡片，否则超时失败。
"""

from typing import Any, Dict, Optional

from loguru import logger


from core.config import get_settings
from schemas.wecom import WecomReplyContext
from services.wecom.card_builder import WecomCardBuilder


class WecomCardEventHandler:
    """处理 template_card_event 回调"""

    def __init__(self, db):
        self.db = db
        self._settings = get_settings()

    async def handle(
        self,
        event_key: str,
        task_id: str,
        card_type: str,
        selected_items: Optional[Dict[str, Any]],
        user_id: str,
        conversation_id: str,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """根据 event_key 路由到对应处理逻辑。

        Args:
            event_key: 点击的按钮 key
            task_id: 卡片的 task_id
            card_type: 卡片类型
            selected_items: 用户选择项（multiple_interaction / vote_interaction）
            user_id: 系统用户 ID
            conversation_id: 当前对话 ID
            reply_ctx: 回复上下文
        """
        logger.info(
            f"Card event | key={event_key} | task_id={task_id} | "
            f"user_id={user_id}"
        )

        handler = self._EVENT_HANDLERS.get(event_key)
        if handler:
            try:
                await handler(
                    self, user_id, conversation_id, reply_ctx,
                    selected_items,
                )
            except Exception as e:
                logger.error(
                    f"Card event handler error | key={event_key} | error={e}"
                )
                await self._reply_text(reply_ctx, "操作失败，请稍后重试")
        elif event_key == "noop":
            pass
        else:
            logger.warning(f"Unknown card event_key: {event_key}")

    # ── 各事件处理方法 ──────────────────────────────────

    async def _handle_start_chat(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        await self._reply_text(reply_ctx, "有什么可以帮你的？直接发消息给我吧~")

    async def _handle_show_help(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_template_card(
                reply_ctx.req_id, WecomCardBuilder.help_card()
            )

    async def _handle_check_credits(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        from services.credit_service import CreditService
        credit_svc = CreditService(self.db, redis=None)
        balance = await credit_svc.get_balance(user_id)
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_template_card(
                reply_ctx.req_id, WecomCardBuilder.credits_card(balance)
            )

    async def _handle_manage_memory(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        from services.memory_service import MemoryService
        mem_svc = MemoryService()
        memories = await mem_svc.get_all_memories(user_id)
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            if memories:
                card = WecomCardBuilder.memory_list_card(memories)
            else:
                card = WecomCardBuilder.memory_empty_card()
            await ws.send_template_card(reply_ctx.req_id, card)

    async def _handle_clear_all_memory(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        from services.memory_service import MemoryService
        mem_svc = MemoryService()
        await mem_svc.delete_all_memories(user_id)
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_update_card(
                reply_ctx.req_id, WecomCardBuilder.memory_cleared_card()
            )

    async def _handle_switch_model(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        """发送模型选择卡片"""
        from services.wecom.wecom_message_service import WecomMessageService
        current = WecomMessageService.get_session_setting(conv_id, "model")
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_template_card(
                reply_ctx.req_id,
                WecomCardBuilder.model_select_card(current_model=current),
            )

    async def _handle_submit_model(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, selected_items: Any,
    ) -> None:
        """处理模型选择提交"""
        model_id = self._extract_selected_id(selected_items, "model_select")
        if not model_id:
            await self._reply_text(reply_ctx, "请选择一个模型后再提交")
            return

        from services.wecom.card_builder import WECOM_MODEL_OPTIONS
        model_name = model_id
        for m in WECOM_MODEL_OPTIONS:
            if m["id"] == model_id:
                model_name = m["text"]
                break

        from services.wecom.wecom_message_service import WecomMessageService
        WecomMessageService.set_session_setting(conv_id, "model", model_id)

        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_update_card(
                reply_ctx.req_id,
                WecomCardBuilder.model_switched_card(model_name),
            )

    async def _handle_new_conversation(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        from services.conversation_service import ConversationService
        conv_svc = ConversationService(self.db)
        await conv_svc.create_conversation(user_id, title="企微对话", model_id="auto")
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_template_card(
                reply_ctx.req_id, WecomCardBuilder.new_conversation_card()
            )

    async def _handle_toggle_thinking(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        from services.wecom.wecom_message_service import WecomMessageService
        current = WecomMessageService.get_session_setting(conv_id, "thinking_mode")
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_template_card(
                reply_ctx.req_id,
                WecomCardBuilder.thinking_mode_card(current or "fast"),
            )

    async def _handle_thinking_deep(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        from services.wecom.wecom_message_service import WecomMessageService
        WecomMessageService.set_session_setting(conv_id, "thinking_mode", "deep")
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_update_card(
                reply_ctx.req_id,
                WecomCardBuilder.thinking_switched_card("deep"),
            )

    async def _handle_thinking_fast(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        from services.wecom.wecom_message_service import WecomMessageService
        WecomMessageService.set_session_setting(conv_id, "thinking_mode", "fast")
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_update_card(
                reply_ctx.req_id,
                WecomCardBuilder.thinking_switched_card("fast"),
            )

    async def _handle_gen_confirm(
        self, user_id: str, conv_id: str,
        reply_ctx: WecomReplyContext, _sel: Any,
    ) -> None:
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_update_card(
                reply_ctx.req_id,
                WecomCardBuilder.generation_confirmed_card("内容"),
            )

    # ── 工具方法 ──────────────────────────────────────

    @staticmethod
    def _extract_selected_id(
        selected_items: Optional[Dict], question_key: str
    ) -> Optional[str]:
        """从 selected_items 中提取指定 question_key 的第一个 option_id"""
        if not selected_items:
            return None
        for item in selected_items.get("selected_item", []):
            if item.get("question_key") == question_key:
                ids = item.get("option_ids", {}).get("option_id", [])
                return ids[0] if ids else None
        return None

    @staticmethod
    async def _reply_text(reply_ctx: WecomReplyContext, text: str) -> None:
        """快捷文本回复"""
        ws = reply_ctx.ws_client
        if ws and reply_ctx.req_id:
            await ws.send_reply(reply_ctx.req_id, "text", {"content": text})

    # ── event_key → handler 映射表 ──────────────────

    _EVENT_HANDLERS = {
        "start_chat": _handle_start_chat,
        "show_help": _handle_show_help,
        "check_credits": _handle_check_credits,
        "manage_memory": _handle_manage_memory,
        "clear_all_memory": _handle_clear_all_memory,
        "switch_model": _handle_switch_model,
        "submit_model": _handle_submit_model,
        "new_conversation": _handle_new_conversation,
        "toggle_thinking": _handle_toggle_thinking,
        "thinking_deep": _handle_thinking_deep,
        "thinking_fast": _handle_thinking_fast,
        "gen_confirm": _handle_gen_confirm,
    }
