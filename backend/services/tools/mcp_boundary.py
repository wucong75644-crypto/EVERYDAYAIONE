"""Bound remote data; never import descriptions, annotations or execution hints."""
import json

from .mcp_allowlist import INPUT_SCHEMA, TOOLS
from .spec import thaw

MAX_WIRE_BYTES = 65536


class MCPError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class MCPTimeoutError(TimeoutError):
    code = "MCP_TIMEOUT"

    def __init__(self):
        super().__init__(self.code)


def bounded_json(value):
    def visit(item, depth=0):
        if depth > 8:
            raise MCPError("MCP_PAYLOAD_LIMIT")
        if isinstance(item, dict):
            if len(item) > 64 or any(not isinstance(k, str) or len(k) > 256 for k in item):
                raise MCPError("MCP_PAYLOAD_LIMIT")
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 64:
                raise MCPError("MCP_PAYLOAD_LIMIT")
            for child in item:
                visit(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise MCPError("MCP_PROTOCOL_ERROR")
    visit(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise MCPError("MCP_PROTOCOL_ERROR") from None
    if len(encoded.encode()) > MAX_WIRE_BYTES:
        raise MCPError("MCP_PAYLOAD_LIMIT")
    return encoded


def normalize_schema(raw):
    """Support only the reviewed simple object schema; no refs, URLs or hooks."""
    bounded_json(raw)
    if not isinstance(raw, dict) or set(raw) - {"type", "properties", "required", "additionalProperties", "description", "title"}:
        raise MCPError("MCP_SCHEMA_NOT_REVIEWED")
    properties = raw.get("properties")
    if not isinstance(properties, dict):
        raise MCPError("MCP_SCHEMA_NOT_REVIEWED")
    normalized = {k: raw[k] for k in ("type", "required", "additionalProperties") if k in raw}
    normalized["properties"] = {}
    for name, field in properties.items():
        if not isinstance(field, dict) or set(field) - {"type", "enum", "description", "title"}:
            raise MCPError("MCP_SCHEMA_NOT_REVIEWED")
        normalized["properties"][name] = {k: field[k] for k in ("type", "enum") if k in field}
    if normalized != thaw(INPUT_SCHEMA):
        raise MCPError("MCP_SCHEMA_NOT_REVIEWED")
    return normalized


def discover_specs(result):
    bounded_json(result)
    if not isinstance(result, dict) or result.get("nextCursor") or not isinstance(result.get("tools"), list):
        raise MCPError("MCP_DISCOVERY_INVALID")
    found = {}
    for remote in result["tools"]:
        if not isinstance(remote, dict) or not isinstance(remote.get("name"), str):
            raise MCPError("MCP_DISCOVERY_INVALID")
        name = remote["name"]
        if name not in TOOLS or name in found:
            raise MCPError("MCP_TOOL_NOT_ALLOWLISTED")
        normalize_schema(remote.get("inputSchema"))
        # Entirely ignore server description/title/annotation/outputSchema.
        found[name] = TOOLS[name].spec()
    if set(found) != set(TOOLS):
        raise MCPError("MCP_DISCOVERY_INVALID")
    return tuple(found.values())


def normalize_result(result, *, redact_values=()):
    bounded_json(result)
    if not isinstance(result, dict) or type(result.get("isError", False)) is not bool:
        raise MCPError("MCP_RESULT_INVALID")
    if result.get("isError"):
        raise MCPError("MCP_TOOL_ERROR")
    content = result.get("content")
    if not isinstance(content, list) or not content or len(content) > 16:
        raise MCPError("MCP_RESULT_INVALID")
    texts = []
    for item in content:
        if (not isinstance(item, dict) or item.get("type") != "text" or
                not isinstance(item.get("text"), str) or len(item["text"].encode()) > 8192):
            raise MCPError("MCP_RESULT_INVALID")
        text = item["text"]
        for secret in redact_values:
            if isinstance(secret, str) and secret:
                text = text.replace(secret, "[REDACTED]")
        texts.append(text)
    # No resource links, images, generated ToolSpec, emit payloads or model role.
    return bounded_json({"connector": "test-readonly", "untrusted_data": True, "text": texts})
