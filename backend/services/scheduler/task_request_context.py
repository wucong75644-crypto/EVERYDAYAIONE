"""Recover only the adjacent clarification of the current creation request.

Source text remains authoritative. No model paraphrase, tool output, completed
request, or process-wide draft is reused as the user's execution instructions.
"""
import re
from typing import Any

_CREATE = re.compile(r"(?:创建|新建|设置|设定|添加|建立|安排).{0,12}(?:任务|日报|周报|月报)")
_CLARIFICATION = re.compile(r"几点|什么时候|哪个|哪些|哪天|谁|什么时间|请.{0,8}(?:补充|提供|确认|选择|填写)")
_FIELDS = re.compile(r"时间|几点|每天|频率|执行|任务|内容|店铺|收件|推送|发给|发送|对象")
_CANCEL = re.compile(r"取消|算了|不用了|不创建|不要创建|换个话题")


def _text(message: dict[str, Any]) -> str:
    content = message.get('content')
    if isinstance(content, list):
        # UserLayer's first text is the user's input; subsequent parts can be
        # generated attachment references and are not instruction authority.
        content = next((part['text'] for part in content if isinstance(part, dict)
                        and part.get('type') == 'text' and isinstance(part.get('text'), str)), '')
    return content.strip() if isinstance(content, str) else ''


def current_creation_text(messages: list[dict[str, Any]] | None) -> str:
    messages = messages or []
    latest = next((i for i in range(len(messages) - 1, -1, -1)
                   if messages[i].get('role') == 'user'), None)
    if latest is None:
        return ''
    current = _text(messages[latest])
    if not current or _CREATE.search(current) or _CANCEL.search(current):
        return current
    turns = [current]
    index = latest - 1
    # Walk only consecutive user/clarification pairs, never an arbitrary chat
    # history window. A tool execution is a boundary, including a returned form.
    while index >= 1 and len(turns) < 8:
        answer, source = messages[index], messages[index - 1]
        question = _text(answer)
        if (answer.get('role') != 'assistant' or answer.get('tool_calls')
                or source.get('role') != 'user' or not _CLARIFICATION.search(question)
                or not _FIELDS.search(question)):
            break
        original = _text(source)
        if not original or _CANCEL.search(original):
            break
        turns.append(original)
        if _CREATE.search(original):
            return '\n'.join(reversed(turns))
        index -= 2
    return current
