"""统一内容编排与展示所有权。

这个模块只负责识别重复的表现层产物，不负责渲染表格、图表或文件。
结构化 block 仍由现有协议和前端组件渲染；不支持结构化内容的渠道再使用
自己的既有 fallback。这里的职责是让同一份业务事实只保留一个主展示出口。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any


_STRUCTURED_TYPES = {"table", "chart", "diagram", "image", "file"}
_TABLE_HEADER_ALIASES = {
    "分组": "dimension",
    "平台": "dimension",
    "渠道": "dimension",
    "组别": "dimension",
    "group": "dimension",
    "platform": "dimension",
    "channel": "dimension",
    "总订单": "total_orders",
    "总订单数": "total_orders",
    "订单总数": "total_orders",
    "订单数": "total_orders",
    "totalorders": "total_orders",
    "有效订单": "valid_orders",
    "有效订单数": "valid_orders",
    "有效订单量": "valid_orders",
    "validorders": "valid_orders",
    "总金额": "total_amount",
    "订单总金额": "total_amount",
    "销售总额": "total_amount",
    "总销售额": "total_amount",
    "totalamount": "total_amount",
    "有效金额": "valid_amount",
    "有效销售额": "valid_amount",
    "有效订单金额": "valid_amount",
    "validamount": "valid_amount",
}
_AGGREGATE_ROW_LABELS = {"合计", "总计", "total", "grandtotal", "all"}
_TABLE_DIMENSION_ALIASES = {
    "pdd": "拼多多",
    "拼多多": "拼多多",
    "jd": "京东",
    "京东": "京东",
    "tb": "淘宝",
    "淘宝": "淘宝",
    "fxg": "抖音",
    "抖音": "抖音",
    "kuaishou": "快手",
    "快手": "快手",
    "xhs": "小红书",
    "小红书": "小红书",
    "sys": "system",
    "系统": "system",
    "系统补发换货线下": "system",
}
_NUMERIC_CELL_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?$"
)
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
    总结误判成重复内容。它只做受约束的指纹/语义等价判断，不生成任何渠道
    渲染内容。
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
    table_blocks = [block for block in blocks if block.get("type") == "table"]
    if table_blocks:
        result = _remove_duplicate_tables(result, table_blocks)

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
    structured = _canonicalize_structured_blocks(copied)
    structured_ids = {id(block) for block in structured}
    result: list[dict[str, Any]] = []
    for block in copied:
        if block.get("type") in _STRUCTURED_TYPES:
            if id(block) in structured_ids:
                result.append(block)
            continue
        if block.get("type") != "text":
            result.append(block)
            continue
        text = canonicalize_text(str(block.get("text") or ""), structured)
        if text:
            block["text"] = text
            result.append(block)
    return result


def _canonicalize_structured_blocks(
    blocks: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按业务产物身份去重结构化 block，保留首次出现的顺序。"""
    result: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") not in _STRUCTURED_TYPES:
            continue
        if any(_structured_blocks_are_equivalent(block, existing) for existing in result):
            continue
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
        self._structured_blocks = _canonicalize_structured_blocks(
            [
                dict(block)
                for block in initial_blocks
                if isinstance(block, Mapping)
            ]
        )
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
            is_new = self._register_structured_block(block_copy)
            await self._flush_pending_text()
            if not is_new:
                return
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

    def _register_structured_block(self, block: dict[str, Any]) -> bool:
        if any(
            _structured_blocks_are_equivalent(block, existing)
            for existing in self._structured_blocks
        ):
            return False
        self._structured_blocks.append(block)
        return True

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
    table_blocks: Iterable[Mapping[str, Any]],
) -> str:
    blocks = list(table_blocks)
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
        if any(_tables_are_equivalent(candidate, block) for block in blocks):
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
    parsed = _parse_markdown_table(text)
    if parsed is None:
        return None
    columns, rows = parsed
    return (tuple(columns), rows)


def _parse_markdown_table(
    text: str,
) -> tuple[list[str], tuple[tuple[str, ...], ...]] | None:
    """解析一个 Markdown 表格，不承担任何渲染职责。"""
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
    if not columns or any(len(row) != len(columns) for row in rows):
        return None
    return columns, rows


def _tables_are_equivalent(
    markdown: str,
    structured: Mapping[str, Any],
) -> bool:
    """判断 Markdown 是否是结构化表格的另一种表现。

    精确指纹优先；语义匹配只在列语义、数据行数量和全部核心数值都一致时
    命中。这样允许模型翻译列名、调整列/行顺序和格式化数字，但不会因为
    两张表“看起来都像数字表”就删除合法的第二张分析表。
    """
    if _markdown_table_fingerprint(markdown) == _table_fingerprint(structured):
        return True

    parsed = _parse_markdown_table(markdown)
    if parsed is None:
        return False
    markdown_columns, markdown_rows = parsed
    structured_columns = [
        str(value).strip() for value in structured.get("columns", [])
    ]
    structured_rows = [
        [str(row.get(column, "")).strip() for column in structured_columns]
        for row in structured.get("rows", [])
        if isinstance(row, Mapping)
    ]
    if not structured_columns or not structured_rows:
        return False
    if len(markdown_columns) != len(structured_columns):
        return False

    markdown_keys = [_table_header_key(value) for value in markdown_columns]
    structured_keys = [_table_header_key(value) for value in structured_columns]
    if any(key is None for key in markdown_keys + structured_keys):
        return False
    if Counter(markdown_keys) != Counter(structured_keys):
        return False

    markdown_index = {
        key: index for index, key in enumerate(markdown_keys)
    }
    structured_data_keys = [
        key for key in structured_keys if key != "dimension"
    ]
    if len(structured_data_keys) < 2:
        return False

    def row_signature(
        row: list[str] | tuple[str, ...],
        index_by_key: Mapping[str, int],
    ) -> tuple[tuple[str, ...], str]:
        data = tuple(
            _normalize_table_cell(row[index_by_key[key]])
            for key in structured_data_keys
        )
        dimension_index = index_by_key.get("dimension")
        dimension = (
            _normalize_table_dimension(row[dimension_index])
            if dimension_index is not None
            else ""
        )
        return data, dimension

    structured_index = {
        key: index for index, key in enumerate(structured_keys)
    }
    structured_signatures = Counter(
        row_signature(row, structured_index)
        for row in structured_rows
        if not _is_aggregate_row(row, structured_index)
    )
    markdown_signatures = Counter(
        row_signature(row, markdown_index)
        for row in markdown_rows
        if not _is_aggregate_row(row, markdown_index)
    )
    return bool(structured_signatures) and structured_signatures == markdown_signatures


def _table_header_key(value: Any) -> str | None:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.strip().casefold()
    normalized = re.sub(r"[\s\-_:：/（）()\[\]【】]", "", normalized)
    normalized = re.sub(r"(?:人民币|元|rmb|cny|￥|¥)$", "", normalized)
    if not normalized:
        return None
    return _TABLE_HEADER_ALIASES.get(normalized, normalized)


def _normalize_table_cell(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    numeric = normalized.replace(",", "").replace("，", "")
    numeric = numeric.replace("￥", "").replace("¥", "").strip()
    if _NUMERIC_CELL_RE.fullmatch(numeric):
        percent = numeric.endswith("%")
        if percent:
            numeric = numeric[:-1]
        try:
            result = Decimal(numeric).normalize()
            return f"number:{result}{'%' if percent else ''}"
        except InvalidOperation:
            pass
    compact = re.sub(r"\s+", "", normalized).casefold()
    return f"text:{compact}"


def _normalize_table_dimension(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"[\s_\-（）()、/]+", "", normalized).casefold()
    return _TABLE_DIMENSION_ALIASES.get(normalized, normalized)


def _is_aggregate_row(
    row: list[str] | tuple[str, ...],
    index_by_key: Mapping[str, int],
) -> bool:
    dimension_index = index_by_key.get("dimension")
    if dimension_index is None or dimension_index >= len(row):
        return False
    label = unicodedata.normalize("NFKC", str(row[dimension_index] or ""))
    label = re.sub(r"[\s_\-]", "", label).casefold()
    return label in _AGGREGATE_ROW_LABELS


def _structured_blocks_are_equivalent(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_type = left.get("type")
    if left_type != right.get("type"):
        return False
    if left_type == "table":
        return _structured_tables_are_equivalent(left, right)
    if left_type in {"chart", "diagram"}:
        return _structured_fingerprint(left) == _structured_fingerprint(right)
    if left_type in {"image", "file"}:
        left_urls = _block_urls(left)
        right_urls = _block_urls(right)
        if left_urls and right_urls:
            return bool(left_urls & right_urls)
        return _stable_json(left) == _stable_json(right)
    return False


def _structured_tables_are_equivalent(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    if _table_fingerprint(left) == _table_fingerprint(right):
        return True
    return _tables_are_equivalent(_table_as_markdown(right), left)


def _table_as_markdown(block: Mapping[str, Any]) -> str:
    columns = [str(value) for value in block.get("columns", [])]
    lines = [
        "| " + " | ".join(value.replace("|", "\\|") for value in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in block.get("rows", []):
        if not isinstance(row, Mapping):
            continue
        values = [
            str(row.get(column, "")).replace("|", "\\|")
            for column in columns
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _stable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


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
