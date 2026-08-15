"""DB content 字段的图片 URL / 纯文本 / OAI 消息提取（纯函数）。

DB 里 content 可能是 JSON 字符串或 block 列表，三个提取函数处理两种格式。
"""

import hashlib
import json
import re
from typing import Any, Dict, List


# 匹配系统生成的 <attachments> 块（双特征 count="数字" + hint="..."，防误剥用户字面输入）
_ATTACHMENTS_RE = re.compile(
    r'\n*<attachments\s+count="\d+"[^>]*hint="[^"]*">.*?</attachments>\s*',
    flags=re.DOTALL,
)
SAFE_HISTORY_TOOL_OUTPUT_BYTES = 8192


def strip_attachments_xml(text: str) -> str:
    """剥离 user message 里的系统生成 <attachments> XML。

    DB 存的是完整 user content（含 XML，作审计/导出用），
    加载发给 LLM 时移除 XML 让历史 user message 保持纯净。

    防误剥：regex 要求 count="数字" + hint="..." 双特征，
    用户字面输入 <attachments>foo</attachments> 不会被匹中。
    """
    if not text or "<attachments" not in text:
        return text
    return _ATTACHMENTS_RE.sub("", text).rstrip()


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
    """从 DB content 字段提取纯文本，跳过图片/视频 URL。

    末尾 strip_attachments_xml：剥离历史 user message 里的 <attachments> 系统块，
    让发给 LLM 的历史保持纯净（DB 存储不变）。
    """
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return extract_text_from_content(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return strip_attachments_xml(content.strip())
    if isinstance(content, list):
        texts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                text = strip_attachments_xml(part.get("text", "").strip())
                if text:
                    texts.append(text)
        return " ".join(texts)
    return ""


def extract_oai_messages_from_content(
    content: Any,
    role: str,
    ts_prefix: str = "",
    safe_completed_tools_only: bool = False,
    max_tool_output_bytes: int = SAFE_HISTORY_TOOL_OUTPUT_BYTES,
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
      - file (user 角色) → 只追加叙事性附件名，不注入历史 workspace_path。
                     image 由上层 build_context_messages 走多模态 image_url 分支。

    所有 tool_step 的 input/code 优先级：input(JSON) > {code: ...}
    ts_prefix 仅注入到首个 text/assistant 消息上（避免重复噪声）。
    safe_completed_tools_only 用于正常历史：仅恢复成功、闭合且有界的工具对；
    中断恢复保持默认行为，不受该筛选影响。
    """
    msgs: List[Dict[str, Any]] = []

    # 解析 raw content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                blocks = parsed
            else:
                text = strip_attachments_xml(content.strip())
                if text:
                    msgs.append({"role": role, "content": f"{ts_prefix}{text}"})
                return msgs
        except (json.JSONDecodeError, TypeError):
            text = strip_attachments_xml(content.strip())
            if text:
                msgs.append({"role": role, "content": f"{ts_prefix}{text}"})
            return msgs
    elif isinstance(content, list):
        blocks = content
    else:
        return msgs

    file_refs = _extract_user_file_refs(blocks) if role == "user" else []

    ts_consumed = False  # ts_prefix 只用一次，避免每个 text block 重复
    for part in blocks:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")

        if ptype == "text":
            text = strip_attachments_xml((part.get("text") or "").strip())
            if not text:
                continue
            prefix = "" if ts_consumed else ts_prefix
            ts_consumed = True
            msgs.append({"role": role, "content": f"{prefix}{text}"})

        elif ptype == "thinking":
            # thinking 是推理过程，不应回灌给 LLM
            continue

        elif ptype == "tool_step":
            msgs.extend(_project_tool_step(
                part,
                sequence_index=len(msgs),
                safe_completed_only=safe_completed_tools_only,
                max_output_bytes=max_tool_output_bytes,
            ))

        elif ptype == "tool_result":
            if safe_completed_tools_only:
                continue
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

        # 其他类型（image/video/audio/form/chart/...）由上层 build_context_messages
        # 单独处理为多模态格式，这里不动
        # file 块在循环外统一追加到 user text（见下方 file_refs 合并）

    # 把 file 引用合并到最近一条同 role 的 text 消息；若没有 text 消息则新建
    if file_refs:
        refs_text = "\n".join(file_refs)
        target = next(
            (
                m for m in reversed(msgs)
                if m.get("role") == role and isinstance(m.get("content"), str)
            ),
            None,
        )
        if target is not None:
            target["content"] = target["content"] + "\n" + refs_text
        else:
            prefix = "" if ts_consumed else ts_prefix
            msgs.append({"role": role, "content": f"{prefix}{refs_text}"})

    return msgs


def _extract_user_file_refs(blocks: List[Any]) -> List[str]:
    """提取历史用户附件的叙事性名称，不暴露 workspace_path。"""
    refs: List[str] = []
    for part in blocks:
        if not isinstance(part, dict) or part.get("type") != "file":
            continue
        name = (part.get("name") or "").strip()
        if name:
            refs.append(f"[附件] {name}")
    return refs


def _project_tool_step(
    part: Dict[str, Any],
    *,
    sequence_index: int,
    safe_completed_only: bool,
    max_output_bytes: int,
) -> List[Dict[str, Any]]:
    """将单个持久化工具步骤投影为闭合 OAI tool pair。"""
    status = part.get("status")
    if status not in ("completed", "error", "cancelled"):
        return []
    output = part.get("output") or ""
    if safe_completed_only and (
        status != "completed"
        or not isinstance(output, str)
        or not output
        or len(output.encode("utf-8")) > max_output_bytes
        or _contains_sensitive_arguments(part.get("input"))
        or _contains_sensitive_text(output)
    ):
        return []

    tool_name = part.get("tool_name") or "unknown"
    tool_call_id = part.get("tool_call_id") or ""
    if not tool_call_id:
        seed = (
            f"{tool_name}|{part.get('input') or part.get('code') or ''}|"
            f"{sequence_index}"
        )
        tool_call_id = "call_" + hashlib.md5(seed.encode()).hexdigest()[:24]
    arguments = part.get("input") or ""
    if not arguments and part.get("code"):
        arguments = json.dumps({"code": part["code"]}, ensure_ascii=False)
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)

    if status == "error" and not output:
        output = "[工具执行失败]"
    elif status == "cancelled":
        from services.handlers.interrupt_anchor import INTERRUPTED_TOOL_RESULT
        output = INTERRUPTED_TOOL_RESULT.format(tool_name=tool_name)
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": arguments},
            }],
        },
        {"role": "tool", "tool_call_id": tool_call_id, "content": output},
    ]


def _contains_sensitive_arguments(arguments: Any) -> bool:
    """检测工具参数中的常见凭证字段；命中时整对不回灌历史。"""
    if not arguments:
        return False
    raw_arguments = arguments if isinstance(arguments, str) else ""
    try:
        value = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError):
        return _contains_sensitive_text(raw_arguments)

    sensitive = re.compile(
        r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)",
        flags=re.IGNORECASE,
    )

    def contains(item: Any) -> bool:
        if isinstance(item, dict):
            return any(
                sensitive.search(str(key)) is not None or contains(child)
                for key, child in item.items()
            )
        if isinstance(item, list):
            return any(contains(child) for child in item)
        return False

    return contains(value)


def _contains_sensitive_text(text: str) -> bool:
    """识别正文中带值的常见凭证表达式。"""
    if not text:
        return False
    return re.search(
        r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)"
        r"[\"']?\s*[=:]\s*[\"']?[^\s,\"'}]{4,}"
        r"|bearer\s+[^\s,\"'}]{4,}",
        text,
    ) is not None
