"""Validate the original JSON result and optional appended batch metadata."""
import json


def parse_json_tool_content(content):
    result, end = json.JSONDecoder().raw_decode(content)
    suffix = content[end:]
    if suffix:
        assert suffix.startswith("\n")
        metadata = json.loads(suffix)
        assert set(metadata) == {"platform_metadata"}
        facts = metadata["platform_metadata"]
        assert facts["source"] == "platform_tool_context"
        assert isinstance(facts["state"], str)
        assert facts["state"].startswith("[工具循环上下文]\n")
    return result
