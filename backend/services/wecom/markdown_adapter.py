"""
企微 Markdown 适配器

将 LLM 输出的标准 Markdown 适配为企微各通道兼容格式：
- stream 通道（长连接）：清理 Mermaid 等不支持语法，其余自动渲染
- app 通道（自建应用）：优先 markdown_v2（语法最全），降级 text
- 长消息自动分割（markdown_v2 限 2048 UTF-8 字节）
"""

import re
from typing import List, Tuple

# ── Markdown 语法检测 ──────────────────────────────────

_MARKDOWN_PATTERNS = re.compile(
    r"(?:^|\n)#{1,6}\s"       # 标题
    r"|(?:^|\n)>\s"            # 引用
    r"|(?:^|\n)[-*+]\s"        # 无序列表
    r"|(?:^|\n)\d+\.\s"        # 有序列表
    r"|\*\*[^*]+\*\*"          # 加粗
    r"|\*[^*]+\*"              # 斜体
    r"|`[^`]+`"                # 行内代码
    r"|```"                    # 代码块
    r"|\|.+\|"                 # 表格
    r"|\[.+?\]\(.+?\)"        # 链接
    r"|(?:^|\n)---",           # 分割线
    re.MULTILINE,
)

# Mermaid 代码块（含空行）
_MERMAID_BLOCK = re.compile(
    r"```mermaid\s*\n[\s\S]*?```",
    re.IGNORECASE,
)

# font color 标签
_FONT_COLOR_TAG = re.compile(
    r'<font\s+color="[^"]*">(.*?)</font>',
    re.IGNORECASE | re.DOTALL,
)

# 删除线
_STRIKETHROUGH = re.compile(r"~~(.+?)~~")


def _has_markdown(text: str) -> bool:
    """检测文本是否包含 Markdown 语法"""
    return bool(_MARKDOWN_PATTERNS.search(text))


# ── 通用清理 ──────────────────────────────────────────

def _remove_mermaid(text: str) -> str:
    """移除 Mermaid 代码块，替换为提示"""
    return _MERMAID_BLOCK.sub("[图表请在 Web 端查看]", text)


def _remove_font_color(text: str) -> str:
    """移除 <font color> 标签，保留内部文本"""
    return _FONT_COLOR_TAG.sub(r"\1", text)


def _remove_strikethrough(text: str) -> str:
    """移除删除线标记，保留文本"""
    return _STRIKETHROUGH.sub(r"\1", text)


# ── stream 通道（长连接） ─────────────────────────────

def clean_for_stream(text: str) -> str:
    """
    清理 stream 通道不支持的 Markdown 语法。

    stream.content 企微客户端自动渲染大部分 Markdown，
    仅需清理 Mermaid 等不支持的语法。
    """
    if not text:
        return text
    return _remove_mermaid(text)


# ── app 通道（自建应用） ──────────────────────────────

def adapt_for_app(text: str) -> Tuple[str, str]:
    """
    将 LLM 输出适配为企微自建应用消息格式。

    策略：含 Markdown 语法 → markdown_v2，纯文本 → text

    Returns:
        (adapted_text, msgtype) — msgtype 为 "markdown_v2" 或 "text"
    """
    if not text:
        return (text, "text")

    # 通用清理（两种类型都不支持这些语法）
    cleaned = _remove_mermaid(text)
    cleaned = _remove_font_color(cleaned)
    cleaned = _remove_strikethrough(cleaned)

    if _has_markdown(cleaned):
        return (cleaned, "markdown_v2")

    return (cleaned, "text")


# ── 长消息分割 ────────────────────────────────────────

def split_long_message(
    text: str,
    max_bytes: int = 2000,
) -> List[str]:
    """
    将超长消息按段落分割为多条，保持 Markdown 语法完整性。

    Args:
        text: 待分割文本
        max_bytes: 单条消息最大 UTF-8 字节数（企微限 2048，留余量用 2000）

    Returns:
        分割后的消息列表（至少 1 条）
    """
    if not text:
        return [text] if text is not None else [""]

    # 如果不超限，直接返回
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    # 按双换行分段
    paragraphs = text.split("\n\n")
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para

        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            # 当前段已有内容，先保存
            if current:
                chunks.append(current)
            # 新段落本身是否超限
            if len(para.encode("utf-8")) <= max_bytes:
                current = para
            else:
                # 单段超长，按句子分割
                chunks.extend(_split_paragraph(para, max_bytes))
                current = ""

    if current:
        chunks.append(current)

    return chunks if chunks else [text[:max_bytes]]


def _split_paragraph(text: str, max_bytes: int) -> List[str]:
    """将单个超长段落按句子分割"""
    # 中英文句号、问号、感叹号、换行作为分割点
    sentences = re.split(r"(?<=[。！？.!?\n])", text)
    chunks: List[str] = []
    current = ""

    for sentence in sentences:
        if not sentence:
            continue

        candidate = current + sentence

        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # 单句超长，硬切
            if len(sentence.encode("utf-8")) > max_bytes:
                chunks.extend(_hard_split(sentence, max_bytes))
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current)

    return chunks


def _hard_split(text: str, max_bytes: int) -> List[str]:
    """按字节硬切（保证不截断 UTF-8 字符）"""
    chunks: List[str] = []
    current = ""

    for char in text:
        candidate = current + char
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = char

    if current:
        chunks.append(current)

    return chunks
