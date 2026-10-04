"""工具执行器的会话历史读取能力。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict

from loguru import logger


class ConversationToolMixin:
    async def _get_conversation_context(
        self,
        args: Dict[str, Any],
    ) -> str:
        """读取当前作用域内的近期对话记录。"""
        from core.config import get_settings
        if get_settings().chat_image_async_enabled:
            return await asyncio.to_thread(self._exact_image_history, args)
        from services.message_service import MessageService

        limit = min(args.get("limit", 10), 20)
        service = MessageService(self.db)
        result = await service.get_messages(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            limit=limit,
            org_id=self.org_id,
        )
        messages = result.get("messages", [])
        if not messages:
            return "当前对话暂无历史消息"
        lines = []
        for message in reversed(messages):
            text_parts = []
            image_urls = []
            for part in message.get("content", []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image" and part.get("url"):
                    image_urls.append(part["url"])
            line = f"[{message.get('role', 'unknown')}] {' '.join(text_parts)}"
            if image_urls:
                line += f" [图片: {', '.join(image_urls)}]"
            lines.append(line)
        context_text = "\n".join(lines)
        logger.debug(
            f"get_conversation_context result | conv={self.conversation_id} "
            f"| msg_count={len(messages)} | len={len(context_text)} "
            f"| preview={context_text[:500]}"
        )
        return context_text

    def _exact_image_history(self, args: dict) -> str:
        from services.handlers.chat_image_request import ChatImageInputResolver, REQUEST_KEY
        if (set(args) - {"limit", "message_ids", "task_id", "text_exact"} or self.context_scope != "user"
                or self.workspace_user_id != self.user_id or not self.personal_context_allowed
                or not self.task_id):
            raise PermissionError("IMAGE_HISTORY_SCOPE_DENIED")
        limit=args.get("limit",10)
        ids=args.get("message_ids",[])
        if type(limit) is not int or not 1<=limit<=20 or not isinstance(ids,list) or len(ids)>8:
            raise ValueError("IMAGE_HISTORY_LIMIT_INVALID")
        parent=self.db.table("tasks").select("user_id,org_id,conversation_id,base_context_revision,input_message_id").eq("id",self.task_id).single().execute().data
        if (parent.get("user_id")!=self.user_id or parent.get("org_id")!=self.org_id
                or parent.get("conversation_id")!=self.conversation_id or parent.get("base_context_revision") is None):
            raise PermissionError("IMAGE_HISTORY_PARENT_DENIED")
        resolver=ChatImageInputResolver(self,base_revision=parent["base_context_revision"],input_message_id=str(parent["input_message_id"]))
        if args.get("task_id"):
            row=self.db.table("tasks").select("user_id,org_id,conversation_id,type,status,assistant_message_id,request_params").eq("id",args["task_id"]).single().execute().data
            if (row.get("user_id")!=self.user_id or row.get("org_id")!=self.org_id
                    or row.get("conversation_id")!=self.conversation_id or row.get("type")!="image"
                    or row.get("status") not in {"completed","failed","cancelled"}):
                raise PermissionError("IMAGE_HISTORY_TASK_DENIED")
            resolver._message(str(row["assistant_message_id"]))
            snapshot=(row.get("request_params") or {}).get(REQUEST_KEY) or row.get("request_params") or {}
            prompt=snapshot.get("prompt")
            if not isinstance(prompt,str):
                raise ValueError("IMAGE_HISTORY_PROMPT_UNAVAILABLE")
            return json.dumps({"task_id":args["task_id"],"prompt":prompt,"sha256":hashlib.sha256(prompt.encode()).hexdigest(),
                "mode":snapshot.get("mode"),"model":snapshot.get("model"),"references":snapshot.get("references",[])},ensure_ascii=False)
        if not ids:
            rows=self.db.table("messages").select("id").eq("conversation_id",self.conversation_id).lte("context_revision",parent["base_context_revision"]).order("context_revision",desc=True).limit(limit).execute().data
            ids=[row["id"] for row in rows]
        messages=[]
        for message_id in ids:
            row=resolver._message(message_id)
            content=row.get("content") or []
            if isinstance(content,str):
                try: content=json.loads(content)
                except ValueError: content=[{"type":"text","text":content}]
            parts=[]
            for index,part in enumerate(content):
                if not isinstance(part,dict):
                    continue
                if part.get("type")=="text":
                    text=part.get("text","")
                    parts.append({"type":"text","content_index":index,"text":text,"sha256":hashlib.sha256(text.encode()).hexdigest()})
                elif part.get("type")=="image":
                    parts.append({"type":"image","content_index":index,"message_id":message_id,
                        **{key:part[key] for key in ("url","name","failed","error","task_id","asset_id") if key in part}})
            messages.append({"message_id":message_id,"status":row.get("status"),"parts":parts})
        output={"messages":messages}
        if "text_exact" in args:
            exact=args["text_exact"]
            if not isinstance(exact,str) or not 1<=len(exact)<=20000:
                raise ValueError("IMAGE_HISTORY_PROMPT_INVALID")
            matches=[(message,part) for message in messages for part in message["parts"]
                if part["type"]=="text" and part["text"].count(exact)==1]
            if len(matches)!=1:
                raise ValueError("IMAGE_HISTORY_PROMPT_AMBIGUOUS")
            message,part=matches[0]
            output["selected_prompt"]={"prompt":exact,"source_prompt":{"message_id":message["message_id"],
                "content_index":part["content_index"],"sha256":hashlib.sha256(exact.encode()).hexdigest()}}
        result=json.dumps(output,ensure_ascii=False)
        if len(result.encode())>128000:
            raise ValueError("IMAGE_HISTORY_TOO_LARGE_SELECT_FEWER_MESSAGES")
        return result
