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
        from services.handlers.chat_input_handoff import line_starts
        if (set(args) - {"limit", "message_ids", "task_id", "text_exact", "text_exacts", "selection_scope", "for_mapping"} or self.context_scope != "user"
                or self.workspace_user_id != self.user_id or not self.personal_context_allowed
                or not self.task_id):
            raise PermissionError("IMAGE_HISTORY_SCOPE_DENIED")
        limit=args.get("limit",10)
        if (type(args.get('for_mapping', False)) is not bool
                or args.get('for_mapping') and {'text_exact', 'text_exacts', 'task_id'}.intersection(args)):
            raise ValueError('CHAT_INPUT_READ_ARGUMENTS_INVALID')
        ids=args.get("message_ids",[])
        if type(limit) is not int or not 1<=limit<=20 or not isinstance(ids,list) or len(ids)>8:
            raise ValueError("IMAGE_HISTORY_LIMIT_INVALID")
        parent=self.db.table("tasks").select("id,user_id,org_id,conversation_id,base_context_revision,input_message_id,request_params").eq("id",self.task_id).single().execute().data
        if (parent.get("user_id")!=self.user_id or parent.get("org_id")!=self.org_id
                or parent.get("conversation_id")!=self.conversation_id or parent.get("base_context_revision") is None):
            raise PermissionError("IMAGE_HISTORY_PARENT_DENIED")
        from services.handlers.chat_context.image_sources import legacy_catalog, discovered_image_sources, content_parts
        legacy = legacy_catalog(parent)
        resolver=ChatImageInputResolver(self,base_revision=parent["base_context_revision"],input_message_id=str(parent["input_message_id"]), legacy_sources=legacy)
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
            rows=self.db.table("messages").select("id,created_at").eq("conversation_id",self.conversation_id).lte("context_revision",parent["base_context_revision"]).order("context_revision",desc=True).limit(limit).execute().data
            all_rows = [*rows, *legacy]
            all_rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
            ids=list(dict.fromkeys(row["id"] for row in all_rows))[:limit]
        messages=[]
        for message_id in ids:
            row=resolver._message(message_id)
            content=content_parts(row.get('content'))
            parts=[]
            for index,part in enumerate(content):
                if not isinstance(part,dict):
                    continue
                if part.get("type")=="text":
                    text=part.get("text","")
                    parts.append({"type":"text","content_index":index,"text":text,"sha256":hashlib.sha256(text.encode()).hexdigest(),"line_starts":line_starts(text)})
                elif part.get("type")=="image":
                    source = next(item for item in discovered_image_sources(row, self.db, org_id=self.org_id,
                        owner_id=self.workspace_user_id, scope=self.context_scope) if item["content_index"] == index)
                    parts.append({"type":"image", **source,
                        **{key:part[key] for key in ("url","failed","error") if key in part}})
            messages.append({"message_id":message_id,"status":row.get("status"),"role":row.get('role'),
                             "context_revision":row.get('context_revision'),"parts":parts})
        output={"messages":messages}
        if args.get('for_mapping'):
            from services.handlers.chat_input_handoff import mapping_view
            output['messages'] = [{**message, 'parts': [mapping_view(part) if part['type'] == 'text' else part
                                                      for part in message['parts']]} for message in messages]
            output['mapping_sources'] = True
        reason = ((parent.get("request_params") or {}).get("_image_sources_v1") or {}).get("unavailable_reason")
        if reason:
            output["image_sources_unavailable_reason"] = reason
        if 'text_exact' in args or 'text_exacts' in args:
            from services.handlers.chat.content_tools import select_text_fragments
            try:
                selected = select_text_fragments(messages, args)
            except ValueError as error:
                if str(error) == 'CHAT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS':
                    raise ValueError('IMAGE_HISTORY_PROMPT_AMBIGUOUS') from error
                raise
            compact_messages = []
            for message in messages:
                fragments = [source for source in selected if source['message_id'] == str(message['message_id'])]
                if not fragments:
                    continue
                parts = [part for part in message['parts'] if part['type'] == 'image']
                for source in fragments:
                    original = next(part['text'] for part in message['parts']
                                    if part['type'] == 'text' and part['content_index'] == source['content_index'])
                    selection = source['bundle']['selection']
                    parts.append({'type': 'text', 'content_index': source['content_index'],
                                  'text': original[selection['start']:selection['end']],
                                  'sha256': selection['sha256'], 'selected_prompt_sha256': source['sha256']})
                compact_messages.append({**message, 'parts': parts})
            output['messages'] = compact_messages
            prompts = [{'prompt': source['text'], 'source_prompt': {
                key: value for key, value in source.items() if key != 'text'}} for source in selected]
            if 'text_exacts' in args:
                output['selected_prompts'] = prompts
            else:
                output['selected_prompt'] = prompts[0]
        result=json.dumps(output,ensure_ascii=False)
        if 'text_exact' in args or 'text_exacts' in args or args.get('for_mapping'):
            from services.agent.tool_result_envelope import SOURCE_SELECTION_BUDGET
            if len(result) > SOURCE_SELECTION_BUDGET:
                raise ValueError('IMAGE_HISTORY_SELECTION_TOO_LARGE_SELECT_FEWER_FRAGMENTS')
        if len(result.encode())>128000:
            raise ValueError("IMAGE_HISTORY_TOO_LARGE_SELECT_FEWER_MESSAGES")
        return result
