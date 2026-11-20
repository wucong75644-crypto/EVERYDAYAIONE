"""Bridge preserves MCP messages and never logs or retries credentials."""
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('kie_mcp_bridge', Path(__file__).resolve().parents[2] / 'scripts/kie-mcp-bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


@pytest.fixture
def token_file(tmp_path):
    token = tmp_path / 'token'
    token.write_text('test-private-token-' + 'x' * 40)
    token.chmod(0o600)
    return token


def setup_io(monkeypatch, token_file, messages):
    output = io.StringIO()
    monkeypatch.setattr(bridge.sys, 'argv', ['bridge', 'https://example.com/api/kie-mcp/', str(token_file)])
    monkeypatch.setattr(bridge.sys, 'stdin', io.StringIO(''.join(json.dumps(m) + '\n' for m in messages)))
    monkeypatch.setattr(bridge.sys, 'stdout', output)
    return output


def test_bridge_preserves_protocol_and_explicit_audio(monkeypatch, token_file):
    messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'create_video', 'arguments': {'sound': True}}}]
    output = setup_io(monkeypatch, token_file, messages)
    sent = []

    def open_request(request, timeout):
        sent.append(request)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        if len(sent) == 1:
            result = {'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': '2025-03-26'}}
        elif len(sent) == 2:
            result = None
        else:
            result = {'jsonrpc': '2.0', 'id': 2, 'result': {'isError': True, 'content': []}}
        response.read.return_value = json.dumps(result).encode() if result else b''
        return response

    opener = Mock()
    opener.open.side_effect = open_request
    monkeypatch.setattr(bridge.urllib.request, 'build_opener', Mock(return_value=opener))
    bridge.main()
    assert len(sent) == 3
    assert sent[-1].get_header('Mcp-protocol-version') == '2025-03-26'
    assert json.loads(sent[-1].data)['params']['arguments']['sound'] is True
    results = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(results) == 2
    assert results[-1]['result']['isError'] is True
    assert token_file.read_text() not in output.getvalue()
    assert bridge.NoRedirect().redirect_request(None, None, None, None, None, 'https://other.example') is None


def test_bridge_never_retries_uncertain_creation(monkeypatch, token_file):
    output = setup_io(monkeypatch, token_file, [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call'}])
    opener = Mock()
    opener.open.side_effect = OSError(token_file.read_text())
    monkeypatch.setattr(bridge.urllib.request, 'build_opener', Mock(return_value=opener))
    bridge.main()
    opener.open.assert_called_once()
    assert json.loads(output.getvalue())['error']['code'] == -32000
    assert token_file.read_text() not in output.getvalue()


def test_bridge_rejects_public_credential_file(monkeypatch, token_file):
    token_file.chmod(0o644)
    setup_io(monkeypatch, token_file, [])
    with pytest.raises(SystemExit, match='0600'):
        bridge.main()
