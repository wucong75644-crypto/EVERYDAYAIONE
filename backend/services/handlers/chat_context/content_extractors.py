"""DB content 字段的图片 URL / 纯文本 / OAI 消息提取（纯函数）。

DB 里 content 可能是 JSON 字符串或 block 列表，三个提取函数处理两种格式。
"""

import hashlib
import json
from typing import Any, Dict, List


def extract_image_urls_from_content(content: Any) -> List[str]:
    """从 DB content 字段提取图片 URL 列表"""
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return extract_image_urls_from_content(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return []
    if isinstance(content, list):
        return [
            part["url"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "image"
            and part.get("url")
        ]
    return []


def extract_interrupt_marker(content: Any) -> Dict[str, Any] | None:
    """从 DB content 字段提取 interrupt_marker block（如有）。

    用于 history_loader 检测用户中断标记，注入 [任务恢复] 前缀。
    详见 docs/document/TECH_用户中断与恢复机制.md §15.5
    """
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                content = parsed
            else:
                return None
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "interrupt_marker":
            return block
    return None


def extract_text_from_content(content: Any) -> str:
    """从 DB content 字段提取纯文本，跳过图片/视频 URL"""
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return extract_text_from_content(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return content.strip()
    if isinstance(content, list):
        texts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                text = part.get("text", "").strip()
                if text:
                    texts.append(text)
        return " ".join(texts)
    return ""


def extract_oai_messages_from_content(
    content: Any,
    role: str,
    ts_prefix: str = "",
) -> List[Dict[str, Any]]:
    """把一条 DB 消息的 content blocks 拆成多条 OpenAI 标准消息。

    DB 里 content 是结构化的 block 列表（text / thinking / tool_step / ...），
    旧的 extract_text_from_content 会把它们压成一段 plain text 注入 history，
    导致 LLM 把代码当模板复用 + 工具调用细节丢失（跨轮失忆）。

    本方法按 block 顺序展开为标准 OAI 消息：
      - text         → {role: <user/assistant>, content: "<ts_prefix><text>"}
      - thinking     → 跳过（不发回 LLM）
      - tool_step (completed/error)
                     → {role: "assistant", tool_calls: [{id, function: {name, arguments}}]}
                     → {role: "tool", tool_call_id, content: "<output>"}
      - tool_step (running) → 跳过（未完成无意义）
      - tool_result  → 退化为 assistant content（无 tool_call_id 无法配对）

    所有 tool_step 的 input/code 优先级：input(JSON) > {code: ...}
    ts_prefix 仅注入到首个 text/assistant 消息上（避免重复噪声）。
    """
    msgs: List[Dict[str, Any]] = []

    # 解析 raw content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                blocks = parsed
            else:
                text = content.strip()
                if text:
                    msgs.append({"role": role, "content": f"{ts_prefix}{text}"})
                return msgs
        except (json.JSONDecodeError, TypeError):
            text = content.strip()
            if text:
                msgs.append({"role": role, "content": f"{ts_prefix}{text}"})
            return msgs
    elif isinstance(content, list):
        blocks = content
    else:
        return msgs

    ts_consumed = False  # ts_prefix 只用一次，避免每个 text block 重复
    for part in blocks:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")

        if ptype == "text":
            text = (part.get("text") or "").strip()
            if not text:
                continue
            prefix = "" if ts_consumed else ts_prefix
            ts_consumed = True
            msgs.append({"role": role, "content": f"{prefix}{text}"})

        elif ptype == "thinking":
            # thinking 是推理过程，不应回灌给 LLM
            continue

        elif ptype == "tool_step":
            status = part.get("status")
            if status not in ("completed", "error", "cancelled"):
                continue
            tool_name = part.get("tool_name") or "unknown"
            tool_call_id = part.get("tool_call_id") or ""
            if not tool_call_id:
                # 历史数据兜底：生成稳定 id
                seed = f"{tool_name}|{part.get('input') or part.get('code') or ''}|{len(msgs)}"
                tool_call_id = "call_" + hashlib.md5(seed.encode()).hexdigest()[:24]
            arguments = part.get("input") or ""
            if not arguments and part.get("code"):
                arguments = json.dumps({"code": part["code"]}, ensure_ascii=False)
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)

            # assistant 消息携带 tool_calls
            msgs.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                }],
            })
            # 紧跟的 tool 消息（OpenAI 协议要求配对）
            output = part.get("output") or ""
            if status == "error" and not output:
                output = "[工具执行失败]"
            elif status == "cancelled":
                from services.handlers.interrupt_anchor import INTERRUPTED_TOOL_RESULT
                output = INTERRUPTED_TOOL_RESULT.format(tool_name=tool_name)
            msgs.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": output,
            })

        elif ptype == "tool_result":
            # 独立的 tool_result block 没有 tool_call_id，无法配对到 assistant.tool_calls
            # 退化为 assistant content（保留信息但不进入工具协议链）
            text = (part.get("text") or "").strip()
            if text:
                tool_name = part.get("tool_name") or ""
                prefix = "" if ts_consumed else ts_prefix
                ts_consumed = True
                msgs.append({
                    "role": "assistant",
                    "content": f"{prefix}[工具结论: {tool_name}] {text}",
                })

        # 其他类型（image/video/audio/file/form/chart/...）由上层 build_context_messages
        # 单独处理为多模态格式，这里不动

    return msgs
