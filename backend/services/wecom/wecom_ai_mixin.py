"""
企微 AI 能力 Mixin — Agent Loop 路由 + 图片/视频生成 + 记忆注入

提供 WecomMessageService 的 AI 相关能力：
- Agent Loop / IntentRouter 智能路由
- CHAT / IMAGE / VIDEO 三种生成类型处理
- 记忆预取与注入
- 积分检查与扣除
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart, GenerationType, TextPart
from schemas.wecom import WecomReplyContext
from services.adapters.factory import create_chat_adapter, DEFAULT_MODEL_ID


class WecomAIMixin:
    """AI 路由 + 生成能力（被 WecomMessageService 继承）"""

    # ── Agent Loop 路由 ─────────────────────────────────

    async def _run_agent_loop(
        self,
        user_id: str,
        conversation_id: str,
        content: List[ContentPart],
    ) -> "AgentResult":
        """执行 Agent Loop 路由，失败降级到 IntentRouter → 兜底 CHAT"""
        from services.agent_types import AgentResult

        if self.settings.agent_loop_enabled:
            from services.agent_loop import AgentLoop

            agent = AgentLoop(self.db, user_id, conversation_id)
            try:
                result = await agent.run(content, thinking_mode=None)
                logger.info(
                    f"Wecom agent loop done | type={result.generation_type.value} | "
                    f"turns={result.turns_used}"
                )
                return result
            except Exception as e:
                logger.warning(f"Wecom agent loop failed | error={e!r}")
            finally:
                await agent.close()

        # 降级到 IntentRouter
        from services.intent_router import IntentRouter

        router = IntentRouter()
        try:
            decision = await router.route(content, user_id, conversation_id)
            return AgentResult(
                generation_type=decision.generation_type,
                model=decision.recommended_model or "",
                system_prompt=decision.system_prompt,
                tool_params=decision.tool_params,
                turns_used=0, total_tokens=0,
            )
        except Exception:
            return AgentResult(
                generation_type=GenerationType.CHAT,
                turns_used=0, total_tokens=0,
            )
        finally:
            await router.close()

    async def _build_memory_prompt(
        self, user_id: str, query: str,
    ) -> Optional[str]:
        """构建记忆 system prompt（失败返回 None）"""
        try:
            from services.memory_service import MemoryService
            from services.memory_config import build_memory_system_prompt

            svc = MemoryService(self.db)
            if not await svc.is_memory_enabled(user_id):
                return None

            memories = await svc.get_relevant_memories(user_id, query)
            if not memories:
                return None

            return build_memory_system_prompt(memories)
        except Exception as e:
            logger.warning(f"Wecom memory prompt failed | error={e}")
            return None

    # ── 生成类型处理 ─────────────────────────────────────

    async def _handle_chat_response(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        text_content: str,
        reply_ctx: WecomReplyContext,
        agent_result: "AgentResult",
        memory_prompt: Optional[str],
        image_urls: Optional[List[str]] = None,
    ) -> None:
        """处理 CHAT 类型：direct_reply 直接回复 / 否则流式生成"""
        if agent_result.direct_reply:
            await self._reply_text(reply_ctx, agent_result.direct_reply)
            await self._update_assistant_message(message_id, agent_result.direct_reply)
            return

        from schemas.message import ImagePart
        from services.intent_router import resolve_auto_model

        content_parts: List[ContentPart] = [TextPart(text=text_content)]
        for url in (image_urls or []):
            content_parts.append(ImagePart(url=url))
        model_id = resolve_auto_model(
            agent_result.generation_type, content_parts, agent_result.model,
        )

        messages = await self._build_chat_messages(
            user_id=user_id,
            conversation_id=conversation_id,
            text_content=text_content,
            system_prompt=agent_result.system_prompt,
            memory_prompt=memory_prompt,
            search_context=agent_result.search_context,
            image_urls=image_urls,
        )

        adapter = create_chat_adapter(model_id)
        try:
            await self._stream_and_reply(adapter, messages, reply_ctx, message_id)
        finally:
            await adapter.close()

    async def _handle_image_response(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        text_content: str,
        reply_ctx: WecomReplyContext,
        agent_result: "AgentResult",
    ) -> None:
        """处理 IMAGE 类型：积分检查 → 生成 → 发送到企微"""
        from config.kie_models import calculate_image_cost
        from services.adapters.factory import create_image_adapter
        from services.intent_router import resolve_auto_model

        content_parts = [TextPart(text=text_content)]
        model_id = resolve_auto_model(
            GenerationType.IMAGE, content_parts, agent_result.model,
        )

        prompt = agent_result.tool_params.get("prompt", text_content)
        aspect_ratio = agent_result.tool_params.get("aspect_ratio", "1:1")

        # 积分检查
        cost = calculate_image_cost(model_name=model_id, image_count=1)
        credits_needed = cost["user_credits"]
        balance = self._get_user_balance(user_id)
        if balance < credits_needed:
            await self._reply_credits_insufficient(
                reply_ctx, credits_needed, balance, "图片"
            )
            return

        # 用 stream 显示进度（不 finish，生成完成后再更新文字）
        if reply_ctx.active_stream_id:
            await self._push_stream_chunk(
                reply_ctx, reply_ctx.active_stream_id,
                "正在为你生成图片，请稍等...", finish=False,
            )
        else:
            await self._reply_text(reply_ctx, "正在为你生成图片，请稍等...")

        adapter = create_image_adapter(model_id)
        try:
            result = await adapter.generate(
                prompt=prompt, size=aspect_ratio,
                output_format="png", wait_for_result=True,
            )
        except Exception as e:
            logger.error(f"Wecom image gen failed | error={e}")
            await self._reply_text(reply_ctx, "图片生成失败，请稍后再试。")
            return
        finally:
            await adapter.close()

        urls = getattr(result, "image_urls", []) or []
        if not urls:
            await self._reply_text(reply_ctx, "图片生成失败，未获得结果。")
            return

        self._deduct_credits(user_id, credits_needed, f"Wecom Image: {model_id}")
        await self._send_media_to_wecom(reply_ctx, urls, "image", message_id)

        # 更新进度文字为"图片生成完成"
        if reply_ctx.active_stream_id:
            await self._push_stream_chunk(
                reply_ctx, reply_ctx.active_stream_id,
                "图片生成完成", finish=True,
            )
            reply_ctx.active_stream_id = None

    async def _handle_video_response(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        text_content: str,
        reply_ctx: WecomReplyContext,
        agent_result: "AgentResult",
    ) -> None:
        """处理 VIDEO 类型：积分检查 → 生成 → 发送到企微"""
        from config.kie_models import calculate_video_cost
        from services.adapters.factory import create_video_adapter
        from services.intent_router import resolve_auto_model

        content_parts = [TextPart(text=text_content)]
        model_id = resolve_auto_model(
            GenerationType.VIDEO, content_parts, agent_result.model,
        )

        prompt = agent_result.tool_params.get("prompt", text_content)
        aspect_ratio = agent_result.tool_params.get("aspect_ratio", "landscape")

        # 积分检查
        cost = calculate_video_cost(model_name=model_id, duration_seconds=10)
        credits_needed = cost["user_credits"]
        balance = self._get_user_balance(user_id)
        if balance < credits_needed:
            await self._reply_credits_insufficient(
                reply_ctx, credits_needed, balance, "视频"
            )
            return

        # 用 stream 显示进度（不 finish，生成完成后再更新文字）
        if reply_ctx.active_stream_id:
            await self._push_stream_chunk(
                reply_ctx, reply_ctx.active_stream_id,
                "正在为你生成视频，预计需要 1-2 分钟，请耐心等待...", finish=False,
            )
        else:
            await self._reply_text(reply_ctx, "正在为你生成视频，预计需要 1-2 分钟，请耐心等待...")

        adapter = create_video_adapter(model_id)
        try:
            result = await adapter.generate(
                prompt=prompt, aspect_ratio=aspect_ratio,
                wait_for_result=True,
            )
        except Exception as e:
            logger.error(f"Wecom video gen failed | error={e}")
            await self._reply_text(reply_ctx, "视频生成失败，请稍后再试。")
            return
        finally:
            await adapter.close()

        video_url = getattr(result, "video_url", None)
        if not video_url:
            await self._reply_text(reply_ctx, "视频生成失败，未获得结果。")
            return

        self._deduct_credits(user_id, credits_needed, f"Wecom Video: {model_id}")
        await self._send_media_to_wecom(reply_ctx, [video_url], "video", message_id)

        # 更新进度文字为"视频生成完成"
        if reply_ctx.active_stream_id:
            await self._push_stream_chunk(
                reply_ctx, reply_ctx.active_stream_id,
                "视频生成完成", finish=True,
            )
            reply_ctx.active_stream_id = None

    # ── 媒体发送 + 流式生成 ──────────────────────────────

    async def _send_media_to_wecom(
        self,
        reply_ctx: WecomReplyContext,
        urls: List[str],
        media_type: str,
        message_id: str,
    ) -> None:
        """统一媒体发送（两渠道差异封装）+ 更新 DB 消息"""
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            for url in urls:
                if media_type == "image":
                    await reply_ctx.ws_client.send_reply(
                        req_id=reply_ctx.req_id,
                        msgtype="markdown",
                        content={"content": f"![图片]({url})"},
                    )
                else:
                    await reply_ctx.ws_client.send_reply(
                        req_id=reply_ctx.req_id,
                        msgtype="text",
                        content={"content": f"视频已生成：{url}"},
                    )
        elif reply_ctx.channel == "app":
            from services.wecom.app_message_sender import (
                upload_temp_media, send_image, send_video, send_text,
            )
            for url in urls:
                media_id = await upload_temp_media(url, media_type)
                if media_id:
                    if media_type == "image":
                        await send_image(reply_ctx.wecom_userid, media_id, reply_ctx.agent_id)
                    else:
                        await send_video(reply_ctx.wecom_userid, media_id, agent_id=reply_ctx.agent_id)
                else:
                    label = "图片" if media_type == "image" else "视频"
                    await send_text(reply_ctx.wecom_userid, f"{label}已生成：{url}", reply_ctx.agent_id)

        # 更新 DB 消息
        content_data = [{"type": media_type, "url": url} for url in urls]
        try:
            self.db.table("messages").update({
                "content": content_data, "status": "completed",
            }).eq("id", message_id).execute()
        except Exception as e:
            logger.warning(f"Update media message failed | error={e}")

    async def _handle_chat_fallback(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        text_content: str,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """兜底：默认模型直接聊天（Agent Loop 完全失败时）"""
        adapter = create_chat_adapter(DEFAULT_MODEL_ID)
        try:
            messages = await self._build_chat_messages(
                user_id=user_id,
                conversation_id=conversation_id,
                text_content=text_content,
            )
            await self._stream_and_reply(adapter, messages, reply_ctx, message_id)
        except Exception as e:
            logger.error(f"Wecom chat fallback failed | error={e}")
            await self._reply_text(reply_ctx, "生成回复时遇到了问题，请稍后再试。")
        finally:
            await adapter.close()

    async def _stream_and_reply(
        self,
        adapter: Any,
        messages: List[Dict[str, Any]],
        reply_ctx: WecomReplyContext,
        message_id: str,
    ) -> None:
        """流式生成 + 推送到企微 + 更新 DB（chat 共用）"""
        from services.wecom.markdown_adapter import clean_for_stream

        accumulated_text = ""
        chunk_count = 0

        # 复用已有 stream（_handle_text 已发送"正在思考..."），否则新建
        if reply_ctx.active_stream_id:
            stream_id = reply_ctx.active_stream_id
        else:
            stream_id = str(uuid.uuid4())
            await self._push_stream_chunk(
                reply_ctx, stream_id, "正在思考...", finish=False,
            )

        async for chunk in adapter.stream_chat(messages=messages):
            if chunk.content:
                accumulated_text += chunk.content
                chunk_count += 1
                if chunk_count % 5 == 0:
                    display = clean_for_stream(accumulated_text)
                    await self._push_stream_chunk(
                        reply_ctx, stream_id, display, finish=False,
                    )

        if accumulated_text:
            display = clean_for_stream(accumulated_text)
            await self._push_stream_chunk(
                reply_ctx, stream_id, display, finish=True,
                feedback_id=message_id,
            )
            reply_ctx.active_stream_id = None
            # DB 存原始内容（不清理），Web 前端自行渲染
            await self._update_assistant_message(message_id, accumulated_text)
        else:
            await self._reply_text(reply_ctx, "抱歉，AI 没有生成回复内容。")

    async def _build_chat_messages(
        self,
        user_id: str,
        conversation_id: str,
        text_content: str,
        system_prompt: Optional[str] = None,
        memory_prompt: Optional[str] = None,
        search_context: Optional[str] = None,
        image_urls: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """构建 LLM 消息列表（含路由注入的人设/记忆/搜索上下文 + 多模态）"""
        messages: List[Dict[str, Any]] = []

        if memory_prompt:
            messages.append({"role": "system", "content": memory_prompt})
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
        messages.append({"role": "system", "content": f"当前时间：{now_str}"})

        if search_context:
            messages.append({
                "role": "system",
                "content": f"以下是搜索到的相关信息：\n{search_context}",
            })

        history = await self._get_conversation_history(
            conversation_id, limit=self.settings.chat_context_limit,
        )
        messages.extend(history)

        # 构建用户消息：有图片时用多模态格式
        if image_urls:
            user_content: list = []
            if text_content:
                user_content.append({"type": "text", "text": text_content})
            for url in image_urls:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": text_content})

        return messages

    # ── 图片 msg_item 构建 ──────────────────────────────

    async def _build_image_msg_items(
        self, urls: List[str],
    ) -> List[Dict[str, Any]]:
        """下载图片并构建 msg_item 列表（base64 + md5）"""
        import base64
        import hashlib
        import httpx

        items: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=15) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    data = resp.content
                    items.append({
                        "msgtype": "image",
                        "image": {
                            "base64": base64.b64encode(data).decode(),
                            "md5": hashlib.md5(data).hexdigest(),
                        },
                    })
                except Exception as e:
                    logger.warning(f"Image download for msg_item failed | error={e}")
        return items

    # ── 积分 ────────────────────────────────────────────

    def _get_user_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        try:
            result = self.db.table("users").select("credits").eq(
                "id", user_id,
            ).single().execute()
            return result.data.get("credits", 0) if result.data else 0
        except Exception:
            return 0

    def _deduct_credits(self, user_id: str, amount: int, reason: str) -> None:
        """直接扣除积分（生成成功后调用）"""
        try:
            self.db.rpc("deduct_credits_atomic", {
                "p_user_id": user_id,
                "p_amount": amount,
                "p_reason": reason,
                "p_change_type": "conversation_cost",
            }).execute()
        except Exception as e:
            logger.warning(f"Wecom credit deduction failed | user_id={user_id} | error={e}")
