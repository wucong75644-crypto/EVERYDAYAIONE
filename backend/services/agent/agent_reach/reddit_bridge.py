"""Fixed isolated bridge to pinned rdt-cli. Inputs arrive on stdin, never argv."""
import json
import re
import sys
from urllib.parse import urlsplit


def execute(payload):
    from rdt_cli.auth import Credential
    from rdt_cli.client import RedditClient
    request = payload['request']
    action = request['action']
    # Upstream names this max_retries but it counts total attempts. One means
    # exactly one request; zero would prevent even the first request.
    with RedditClient(Credential.from_dict(payload['secret']), max_retries=1, timeout=20) as client:
        identity = client.get_me()
        if str(identity.get('id', '')) != payload['account_id']:
            raise ValueError('ACCOUNT_MISMATCH')
        path = urlsplit(request['url']).path
        if action == 'create_post':
            match = re.fullmatch(r'/r/([A-Za-z0-9_]{1,21})/?', path)
            if not match:
                raise ValueError('INVALID_URL')
            response = client._post('/api/submit', data={'sr': match[1], 'kind': 'self',
                'title': request['title'], 'text': request['content'], 'api_type': 'json'})
        else:
            match = re.search(r'/comments/([a-z0-9]+)/', path + '/')
            if not match:
                raise ValueError('INVALID_URL')
            parent = 't3_' + match[1]
            if action == 'set_like':
                response = client.vote(parent, 1 if request['liked'] else 0)
            elif action == 'create_comment':
                response = client.post_comment(parent, request['content'])
            else:
                raise ValueError('UNSUPPORTED_OPERATION')
        if not isinstance(response, dict):
            raise ValueError('WRITE_UNCERTAIN')
        body = response.get('json', response)
        if not isinstance(body, dict) or body.get('errors'):
            raise ValueError('WRITE_UNCERTAIN')
        receipt = {'action': action, 'target': request['url'], 'account_id': payload['account_id']}
        if action != 'set_like':
            data = body.get('data') or {}
            things = data.get('things') or []
            remote_id = data.get('id') or (things[0].get('data', {}).get('id') if things else '')
            if not re.fullmatch(r'[a-z0-9]+', str(remote_id)):
                raise ValueError('WRITE_UNCERTAIN')
            receipt['remote_id'] = remote_id
        else:
            receipt['liked'] = request['liked']
        return {'receipt': receipt}


if __name__ == '__main__':
    try:
        data = sys.stdin.buffer.read(64001)
        if len(data) > 64000:
            raise ValueError('INVALID_ARGUMENTS')
        print(json.dumps(execute(json.loads(data))))
    except Exception:
        # Never print upstream exceptions: they may contain cookies or content.
        print(json.dumps({'ok': False, 'error': {'code': 'write_uncertain'}}))
        sys.exit(1)
