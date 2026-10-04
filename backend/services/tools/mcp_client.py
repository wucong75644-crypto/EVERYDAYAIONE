"""Bounded stdio MCP client for one fixed platform subprocess; no retries."""
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from .mcp_allowlist import CONNECTOR_ID, require_connector
from .mcp_boundary import MCPError, MCPTimeoutError, MAX_WIRE_BYTES, bounded_json, discover_specs

PROTOCOL_VERSION = "2025-11-25"


class MCPClient:
    def __init__(self, connector_id=CONNECTOR_ID, *, bearer_token=None):
        require_connector(connector_id)
        if bearer_token is not None and (
            not isinstance(bearer_token, str) or not bearer_token
            or len(bearer_token) > 4096 or "\n" in bearer_token or "\r" in bearer_token
        ):
            raise MCPError("MCP_CREDENTIAL_UNAVAILABLE")
        self.connector_id = connector_id
        self._bearer_token = bearer_token
        self.process = None
        self._sequence = 0
        from .mcp import MCPConnectorState
        self.state = MCPConnectorState.CONFIGURED

    @asynccontextmanager
    async def session(self):
        try:
            # Fixed reviewed executable path and isolated Python. Only the
            # org-scoped short-lived credential reaches this fixed test server.
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", str(Path(__file__).with_name("mcp_test_server.py")),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"MCP_TEST_BEARER_TOKEN": self._bearer_token} if self._bearer_token else {},
                limit=MAX_WIRE_BYTES + 1,
            )
            await self.initialize()
            yield self
        except MCPError:
            from .mcp import MCPConnectorState
            self.state = MCPConnectorState.ERROR
            raise
        except OSError:
            from .mcp import MCPConnectorState
            self.state = MCPConnectorState.ERROR
            raise MCPError("MCP_UNAVAILABLE") from None
        finally:
            process = self.process
            if process is not None:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
                self.process = None

    async def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write((bounded_json(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params=None):
        self._sequence += 1
        request_id = self._sequence
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self.process.stdin.write((bounded_json(message) + "\n").encode())
            await self.process.stdin.drain()
            for _ in range(16):
                line = await self.process.stdout.readline()
                if not line:
                    raise MCPError("MCP_UNAVAILABLE")
                if len(line) > MAX_WIRE_BYTES:
                    raise MCPError("MCP_PAYLOAD_LIMIT")
                response = json.loads(line)
                bounded_json(response)
                if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
                    raise MCPError("MCP_PROTOCOL_ERROR")
                if "method" in response:
                    # No server-initiated sampling, elicitation or access to roots.
                    if "id" in response:
                        raise MCPError("MCP_SERVER_REQUEST_DENIED")
                    continue
                if type(response.get("id")) is not int or response["id"] != request_id:
                    raise MCPError("MCP_PROTOCOL_ERROR")
                if "error" in response:
                    remote_error = response.get("error")
                    if isinstance(remote_error, dict) and remote_error.get("code") == -32001:
                        raise MCPError("MCP_AUTH_FAILED")
                    raise MCPError("MCP_REMOTE_ERROR")
                if "result" not in response:
                    raise MCPError("MCP_PROTOCOL_ERROR")
                return response["result"]
            raise MCPError("MCP_PAYLOAD_LIMIT")
        except asyncio.CancelledError:
            # Best effort protocol cancellation; teardown kills the child as well.
            try:
                await asyncio.wait_for(self.notify("notifications/cancelled", {"requestId": request_id}), .1)
            except Exception:
                pass
            raise
        except (ValueError, UnicodeError, RecursionError):
            raise MCPError("MCP_PROTOCOL_ERROR") from None
        except (BrokenPipeError, ConnectionError):
            raise MCPError("MCP_UNAVAILABLE") from None

    async def initialize(self):
        result = await self.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
            "capabilities": {}, "clientInfo": {"name": "everydayai", "version": "phase3-step2"}})
        if (not isinstance(result, dict) or result.get("protocolVersion") != PROTOCOL_VERSION
                or not isinstance(result.get("capabilities"), dict)
                or not isinstance(result["capabilities"].get("tools"), dict)):
            raise MCPError("MCP_PROTOCOL_ERROR")
        await self.notify("notifications/initialized")

    async def health(self):
        if await self.request("ping") != {}:
            raise MCPError("MCP_HEALTH_FAILED")
        from .mcp import transition
        self.state = transition(self.state, "healthy", enabled=True)

    async def discover(self):
        return discover_specs(await self.request("tools/list"))


async def bounded_operation(operation, *, timeout=5.0, cancellation=None):
    if cancellation is not None and cancellation.is_set():
        operation.close()
        raise asyncio.CancelledError()
    if timeout <= 0:
        operation.close()
        raise MCPTimeoutError()
    task = asyncio.create_task(operation)
    watcher = asyncio.create_task(cancellation.wait()) if cancellation is not None else None
    try:
        done, _ = await asyncio.wait([task] + ([watcher] if watcher else []),
                                    timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if watcher is not None and watcher in done:
            raise asyncio.CancelledError()
        if task not in done:
            raise MCPTimeoutError()
        return task.result()
    finally:
        for pending in (task, watcher):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(t for t in (task, watcher) if t is not None), return_exceptions=True)
