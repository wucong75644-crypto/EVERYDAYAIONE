"""Reviewed synthetic MCP stdio server. Reads constant data; no files or network."""
import json
import sys

SCHEMA = {"type": "object", "properties": {"record_id": {"type": "string", "enum": ["sample"]}},
          "required": ["record_id"], "additionalProperties": False}


def main():
    initialized = False
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        if method == "notifications/initialized":
            initialized = True
            continue
        if "id" not in request:
            continue
        response = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "everydayai-test-readonly", "version": "1"}}
        elif not initialized:
            response["error"] = {"code": -32000, "message": "Not initialized"}
            result = None
        elif method in {"ping", "tools/list", "tools/call"} and (
            not __import__("os").environ.get("MCP_TEST_BEARER_TOKEN")
            or __import__("os").environ.get("MCP_TEST_BEARER_TOKEN") == "expired"
        ):
            response["error"] = {"code": -32001, "message": "Authentication failed"}
            result = None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": "lookup_sample", "description": "Untrusted server description",
                "inputSchema": SCHEMA, "annotations": {"readOnlyHint": True}}]}
        elif method == "tools/call" and request.get("params", {}).get("name") == "lookup_sample":
            valid = request["params"].get("arguments") == {"record_id": "sample"}
            result = {"isError": not valid, "content": [{"type": "text",
                "text": '{"record_id":"sample","value":"synthetic-only"}' if valid else "Invalid record"}]}
        else:
            response["error"] = {"code": -32601, "message": "Unsupported method"}
            result = None
        if "error" not in response:
            response["result"] = result
        print(json.dumps(response, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
