"""统一内容编排与展示所有权。

这个模块只负责识别重复的表现层产物，不负责渲染表格、图表或文件。
结构化 block 仍由现有协议和前端组件渲染；不支持结构化内容的渠道再使用
自己的既有 fallback。这里的职责是让同一份业务事实只保留一个主展示出口。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


_STRUCTURED_TYPES = {"table", "chart", "diagram", "image", "file"}
_FENCED_BLOCK_RE = re.compile(
    r"(?ms)^[ \t]*```(?P<language>[a-zA-Z0-9_-]*)[ \t]*\n"
    r"(?P<body>.*?)^[ \t]*```[ \t]*$"
)
_MARKDOWN_LINK_RE = re.compile(
    r"!?(?:\[[^\]]*\])\((?P<url><[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)"
)


def canonicalize_text(
    text: str,
    structured_blocks: Iterable[Mapping[str, Any]],
) -> str:
    """删除与结构化 block 相同的 Markdown 表现，保留其他文本。

    该函数是 fail-open 的：无法可靠解析的文本原样保留，避免把模型的
    总结误判成重复内容。它只做指纹比对，不生成任何渠道渲染内容。
    """
    if not text:
        return text
    blocks = [
        dict(block)
        for block in structured_blocks
        if isinstance(block, Mapping)
        and block.get("type") in _STRUCTURED_TYPES
    ]
    if not blocks:
        return text

    result = text
    table_fingerprints = {
        _table_fingerprint(block)
        for block in blocks
        if block.get("type") == "table"
    }
    if table_fingerprints:
        result = _remove_duplicate_tables(result, table_fingerprints)

    fenced_blocks = {
        _structured_fingerprint(block): block
        for block in blocks
        if block.get("type") in {"chart", "diagram"}
    }
    if fenced_blocks:
        result = _remove_duplicate_fenced_blocks(result, fenced_blocks)

    media_urls = {
        url
        for block in blocks
        for url in _block_urls(block)
        if url
    }
    if media_urls:
        result = _remove_duplicate_links(result, media_urls)

    if result == text:
        return text
    return _clean_removed_artifact_whitespace(result)


def canonicalize_content_blocks(
    blocks: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """返回持久化/投递前的 canonical content blocks。"""
    copied = [dict(block) for block in blocks if isinstance(block, Mapping)]
    structured = [
        block for block in copied
        if block.get("type") in _STRUCTURED_TYPES
    ]
    result: list[dict[str, Any]] = []
    for block in copied:
        if block.get("type") != "text":
            result.append(block)
            continue
        text = canonicalize_text(str(block.get("text") or ""), structured)
        if text:
            block["text"] = text
            result.append(block)
    return result


def canonicalize_output(
    text: str,
    blocks: Iterable[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """把独立 text + blocks 组装成一个 canonical 结果。"""
    content = canonicalize_content_blocks([
        {"type": "text", "text": text},
        *blocks,
    ])
    text_parts = [
        str(block.get("text") or "")
        for block in content
        if block.get("type") == "text" and block.get("text")
    ]
    return "\n".join(text_parts), [
        block for block in content if block.get("type") != "text"
    ]


class OutputOrchestrator:
    """在既有 ExecutionSink 前提供展示所有权和流式去重。

    只缓冲疑似媒体/表格 Markdown 的尾段；普通文本仍逐 chunk 透传。
    如果随后收到对应结构化 block，缓冲段会在 block 前被 canonicalize；
    如果没有结构化 block，则在 flush 时原样放行。
    """

    def __init__(
        self,
        sink: Any,
        initial_blocks: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self._sink = sink
        self._structured_blocks = [
            dict(block)
            for block in initial_blocks
            if isinstance(block, Mapping)
            and block.get("type") in _STRUCTURED_TYPES
        ]
        self._pending_text = ""
        self._committed_text = canonicalize_text(
            str(getattr(sink, "text", "") or ""),
            self._structured_blocks,
        )
        self._visible_text = self._committed_text

    @property
    def text(self) -> str:
        return self._visible_text

    @property
    def thinking(self) -> str:
        return str(getattr(self._sink, "thinking", "") or "")

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return list(getattr(self._sink, "blocks", []) or [])

    @property
    def emit_empty_thinking(self) -> bool:
        return bool(getattr(self._sink, "emit_empty_thinking", False))

    async def start(self) -> None:
        await self._sink.start()

    async def on_text(self, text: str) -> None:
        if not text:
            return
        if self._pending_text:
            combined = self._pending_text + text
            if _candidate_can_continue(combined):
                self._pending_text = combined
                self._refresh_visible_text()
                return
            pending = self._pending_text
            self._pending_text = ""
            await self._emit_text(pending)
            await self.on_text(text)
            return
        candidate_start = _structured_markdown_start(text)
        if candidate_start is not None:
            prefix = text[:candidate_start]
            if prefix:
                await self._emit_text(prefix)
            self._pending_text = text[candidate_start:]
            self._refresh_visible_text()
            return
        if self._structured_blocks:
            cleaned = canonicalize_text(text, self._structured_blocks)
            await self._emit_text(cleaned)
            return
        await self._emit_text(text)

    async def on_thinking(self, text: str) -> None:
        await self._sink.on_thinking(text)

    async def on_block(self, block: dict[str, Any]) -> None:
        block_copy = dict(block)
        block_type = block_copy.get("type")
        if block_type in _STRUCTURED_TYPES:
            self._structured_blocks.append(block_copy)
            await self._flush_pending_text()
        elif block_type == "text":
            block_copy["text"] = canonicalize_text(
                str(block_copy.get("text") or ""),
                self._structured_blocks,
            )
            if not block_copy["text"]:
                return
        await self._sink.on_block(block_copy)

    async def on_block_update(self, block: dict[str, Any]) -> None:
        await self._sink.on_block_update(block)

    async def on_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        turn: int,
    ) -> None:
        callback = getattr(self._sink, "on_tool_calls", None)
        if callback is not None:
            await callback(tool_calls, turn)

    async def on_tool_result(self, **kwargs: Any) -> None:
        callback = getattr(self._sink, "on_tool_result", None)
        if callback is not None:
            await callback(**kwargs)

    async def flush(self) -> None:
        await self._flush_pending_text()
        await self._sink.flush()

    async def finalize_text(self) -> None:
        """兼容旧 façade：只收口文本候选，不改变其 stream 生命周期。"""
        await self._flush_pending_text()

    async def flush_progress(self) -> None:
        """保持 Actor checkpoint 的既有入口，不改变 lease/状态语义。"""
        callback = getattr(self._sink, "flush_progress", None)
        if callback is not None:
            await callback()

    def canonicalize_blocks(
        self,
        blocks: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        return canonicalize_content_blocks(blocks)

    async def _flush_pending_text(self) -> None:
        if not self._pending_text:
            return
        pending = self._pending_text
        self._pending_text = ""
        cleaned = canonicalize_text(pending, self._structured_blocks)
        if cleaned:
            await self._emit_text(cleaned)
        else:
            self._refresh_visible_text()

    async def _emit_text(self, text: str) -> None:
        if not text:
            return
        self._committed_text += text
        self._visible_text = self._committed_text
        await self._sink.on_text(text)

    def _refresh_visible_text(self) -> None:
        pending = canonicalize_text(self._pending_text, self._structured_blocks)
        self._visible_text = self._committed_text + pending


def _structured_markdown_start(text: str) -> int | None:
    offset = 0
    if "|" in text:
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines[:-1]):
            if _is_pipe_line(line) and _is_table_separator(lines[index + 1]):
                if index > 0 and _is_heading(lines[index - 1]):
                    return offset - len(lines[index - 1])
                return offset
            offset += len(line)
        offset = 0
        for index, line in enumerate(lines):
            if _is_pipe_line(line):
                if index > 0 and _is_heading(lines[index - 1]):
                    return offset - len(lines[index - 1])
                return offset
            offset += len(line)
    if "```" in text and re.search(
        r"(?m)^\s*```(?:json|text|mermaid|vega|vegalite)\s*$",
        text,
    ):
        match = re.search(
            r"(?m)^\s*```(?:json|text|mermaid|vega|vegalite)\s*$",
            text,
        )
        if match:
            return match.start()
    link_match = _MARKDOWN_LINK_RE.search(text)
    if link_match:
        line_start = text.rfind("\n", 0, link_match.start()) + 1
        return line_start
    return None


def _candidate_can_continue(text: str) -> bool:
    if "\n" not in text:
        return True
    if _markdown_table_fingerprint(text) is not None:
        return True
    if re.search(
        r"(?ms)^\s*```(?:json|text|mermaid|vega|vegalite)\s*$.*?^\s*```\s*$",
        text,
    ):
        return True
    return bool(_MARKDOWN_LINK_RE.search(text))


def _remove_duplicate_tables(
    text: str,
    fingerprints: set[tuple[Any, ...]],
) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    while index < len(lines):
        if not _is_pipe_line(lines[index]):
            output.append(lines[index])
            index += 1
            continue
        if index + 1 >= len(lines) or not _is_table_separator(lines[index + 1]):
            output.append(lines[index])
            index += 1
            continue
        start = index
        if output and _is_heading(output[-1]):
            start = index - 1
        end = index + 2
        while end < len(lines) and _is_pipe_line(lines[end]):
            end += 1
        candidate = "".join(lines[start:end])
        if _markdown_table_fingerprint(candidate) in fingerprints:
            if start == index - 1:
                output.pop()
            index = end
            continue
        output.extend(lines[index:end])
        index = end
    return "".join(output)


def _remove_duplicate_fenced_blocks(
    text: str,
    fingerprints: Mapping[tuple[Any, ...], Mapping[str, Any]],
) -> str:
    def replace(match: re.Match[str]) -> str:
        language = (match.group("language") or "").lower()
        body = match.group("body").strip()
        if language == "json":
            try:
                value = json.loads(body)
            except (TypeError, ValueError):
                return match.group(0)
            for block in fingerprints.values():
                if block.get("type") == "chart" and value == block.get("option", {}):
                    return ""
        if language in {"text", "mermaid"}:
            for block in fingerprints.values():
                if (
                    block.get("type") == "diagram"
                    and body.strip() == str(block.get("source") or "").strip()
                ):
                    return ""
        return match.group(0)

    return _FENCED_BLOCK_RE.sub(replace, text)


def _remove_duplicate_links(text: str, urls: set[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        url = match.group("url").strip("<>")
        return "" if url in urls else match.group(0)

    return _MARKDOWN_LINK_RE.sub(replace, text)


def _markdown_table_fingerprint(text: str) -> tuple[Any, ...] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not _is_heading(line)]
    if len(lines) < 2 or not _is_table_separator(lines[1]):
        return None
    columns = _split_pipe_cells(lines[0])
    rows = tuple(
        tuple(_split_pipe_cells(line))
        for line in lines[2:]
        if _is_pipe_line(line)
    )
    return (tuple(columns), rows)


def _table_fingerprint(block: Mapping[str, Any]) -> tuple[Any, ...]:
    columns = tuple(str(value).strip() for value in block.get("columns", []))
    rows = tuple(
        tuple(str(row.get(column, "")).strip() for column in columns)
        for row in block.get("rows", [])
        if isinstance(row, Mapping)
    )
    return (columns, rows)


def _structured_fingerprint(block: Mapping[str, Any]) -> tuple[Any, ...]:
    block_type = block.get("type")
    if block_type == "chart":
        return (
            "chart",
            json.dumps(block.get("option", {}), ensure_ascii=False, sort_keys=True),
        )
    if block_type == "diagram":
        return ("diagram", str(block.get("source") or "").strip())
    return (str(block_type),)


def _block_urls(block: Mapping[str, Any]) -> set[str]:
    return {
        str(block.get(field)).strip()
        for field in ("url", "original_url", "preview_url", "download_url")
        if block.get(field)
    }


def _is_pipe_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    cells = _split_pipe_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _split_pipe_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped:
        if char == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
            continue
        if char == "\\" and not escaped:
            escaped = True
            continue
        current.append(char)
        escaped = False
    cells.append("".join(current).strip())
    return cells


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^\s*#{1,6}\s+\S", line.strip()))


def _clean_removed_artifact_whitespace(text: str) -> str:
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return text.strip("\n")
