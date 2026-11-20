#!/usr/bin/env python3
"""Stateless MCP stdio-to-HTTPS bridge; credentials stay in a private local file."""
import json
from pathlib import Path
import stat
import sys
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the credential to a redirect destination.
        return None


def main():
    if len(sys.argv) != 3 or not sys.argv[1].startswith('https://'):
        raise SystemExit('Usage: kie-mcp-bridge.py HTTPS_URL PRIVATE_TOKEN_FILE')
    endpoint, filename = sys.argv[1:]
    path = Path(filename)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit('KIE MCP credential file must be private (0600)')
    token = path.read_text().strip()
    if len(token) < 32:
        raise SystemExit('KIE MCP credential is invalid')
    opener = urllib.request.build_opener(NoRedirect)
    protocol = None
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream'}
        if protocol:
            headers['MCP-Protocol-Version'] = protocol
        request = urllib.request.Request(endpoint, data=json.dumps(message).encode(), headers=headers)
        try:
            with opener.open(request, timeout=70) as response:
                body = response.read()
                result = json.loads(body) if body else None
        except (OSError, ValueError):
            # No retries: a failed creation response can still mean the job was submitted.
            result = {'jsonrpc': '2.0', 'id': message.get('id'), 'error': {
                'code': -32000, 'message': 'KIE MCP connection failed. Do not retry uncertain video creation with a new request_key.'}}
        if message.get('method') == 'initialize' and isinstance(result, dict):
            protocol = result.get('result', {}).get('protocolVersion')
        if 'id' in message and result is not None:
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
