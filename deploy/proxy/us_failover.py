"""US subscription conversion and bounded proxy health/failover; no business requests."""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote

import httpx
import yaml

STATE_ROOT = Path('/etc/sing-box/us-failover')
STATUS = Path('/var/lib/everydayai-us-proxy/status.json')
GROUP = 'overseas-ss'
TEST_URL = 'https://api.kie.ai'


def us_nodes(subscription):
    nodes = subscription.get('proxies', [])
    return [node for node in nodes if any(word in str(node.get('name', '')).lower()
        for word in ('美国', '🇺🇸', 'united states', 'usa', 'los angeles', 'seattle', 'san jose'))]


def convert(node):
    if node['type'] not in ('vless', 'trojan'):
        raise ValueError('Unsupported US node protocol')
    tag = 'us-' + hashlib.sha256((node['name']+'|'+node['server']+'|'+str(node['port'])).encode()).hexdigest()[:12]
    out = {'type': node['type'], 'tag': tag, 'server': node['server'], 'server_port': int(node['port'])}
    if node['type'] == 'vless':
        out['uuid'] = node['uuid']
        if node.get('flow'): out['flow'] = node['flow']
    else:
        out['password'] = node['password']
    if node.get('tls', node['type'] == 'trojan'):
        tls = {'enabled': True, 'server_name': node.get('servername') or node.get('sni') or node['server']}
        if node.get('client-fingerprint'):
            tls['utls'] = {'enabled': True, 'fingerprint': node['client-fingerprint']}
        reality = node.get('reality-opts')
        if reality:
            tls['reality'] = {'enabled': True, 'public_key': reality['public-key'], 'short_id': reality.get('short-id', '')}
        out['tls'] = tls
    network = node.get('network', 'tcp')
    if network == 'ws':
        opts = node.get('ws-opts', {})
        out['transport'] = {'type': 'ws', 'path': opts.get('path', '/'), 'headers': opts.get('headers', {})}
    elif network == 'grpc':
        out['transport'] = {'type': 'grpc', 'service_name': node.get('grpc-opts', {}).get('grpc-service-name', '')}
    elif network != 'tcp':
        raise ValueError('Unsupported US node transport')
    return out


def build(base, subscription, secret, *, stage=False):
    cfg = copy.deepcopy(base)
    nodes = us_nodes(subscription)
    if not nodes: raise ValueError('No US nodes')
    converted = [convert(node) for node in nodes]
    if len({node['tag'] for node in converted}) != len(converted): raise ValueError('Duplicate US nodes')
    if len(nodes) > 100: raise ValueError('Too many US nodes')
    tags = {node['tag'] for node in converted}
    cfg['outbounds'] = [node for node in cfg['outbounds'] if node['tag'] != GROUP and node['tag'] not in tags]
    cfg['outbounds'] += converted + [{'type': 'selector', 'tag': GROUP,
        'outbounds': [node['tag'] for node in converted], 'default': converted[0]['tag'],
        'interrupt_exist_connections': False}]
    cfg['inbounds'] = [node for node in cfg['inbounds'] if not node['tag'].startswith('us-health-')]
    if stage:
        for inbound in cfg['inbounds']:
            if inbound.get('listen_port') in (7890, 7891): inbound['listen_port'] += 10000
    rules = [rule for rule in cfg.setdefault('route', {}).get('rules', [])
        if not any(str(tag).startswith('us-health-') for tag in rule.get('inbound', []))]
    meta = []
    for index, (source, node) in enumerate(zip(nodes, converted)):
        port = (28100 if stage else 18100) + index
        inbound = 'us-health-' + node['tag']
        cfg['inbounds'].append({'type': 'mixed', 'tag': inbound, 'listen': '127.0.0.1', 'listen_port': port})
        rules.insert(index, {'inbound': [inbound], 'action': 'route', 'outbound': node['tag']})
        meta.append({'tag': node['tag'], 'name': source['name'], 'port': port})
    cfg['route']['rules'] = rules
    controller = '127.0.0.1:' + ('29099' if stage else '19099')
    cfg.setdefault('experimental', {})['clash_api'] = {'external_controller': controller, 'secret': secret}
    return cfg, {'nodes': meta, 'controller': 'http://'+controller, 'secret': secret, 'group': GROUP}


def choose(tags, current, healthy):
    if current in healthy: return current
    if current in tags:
        index = tags.index(current) + 1
        tags = tags[index:] + tags[:index]
    return next((tag for tag in tags if tag in healthy), None)


def atomic_json(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


async def probe_nodes(meta):
    semaphore = asyncio.Semaphore(4)
    async def probe(node):
        async with semaphore:
            try:
                async with asyncio.timeout(6):
                    async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{node['port']}", trust_env=False,
                            timeout=httpx.Timeout(6, connect=4)) as client:
                        response = await client.get(TEST_URL)
                # Root normally returns 404. 401/403 are not a usable KIE route.
                return node['tag'], response.status_code in (200, 204, 404)
            except (httpx.HTTPError, OSError, TimeoutError):
                return node['tag'], False
    return dict(await asyncio.gather(*(probe(node) for node in meta['nodes'])))


async def check(meta, status_path=STATUS):
    result = await probe_nodes(meta)
    healthy = {tag for tag, ok in result.items() if ok}
    tags = [node['tag'] for node in meta['nodes']]
    async with httpx.AsyncClient(base_url=meta['controller'], trust_env=False, timeout=4,
            headers={'Authorization': 'Bearer '+meta['secret']}) as api:
        endpoint = '/proxies/' + quote(meta['group'], safe='')
        response = await api.get(endpoint); response.raise_for_status()
        current = response.json().get('now')
        selected = choose(tags, current, healthy)
        if selected and selected != current:
            response = await api.put(endpoint, json={'name': selected}); response.raise_for_status()
            response = await api.get(endpoint); response.raise_for_status()
            if response.json().get('now') != selected: raise RuntimeError('Selection confirmation failed')
    previous = {}
    try: previous = json.loads(Path(status_path).read_text())
    except (OSError, ValueError): pass
    failures = previous.get('all_failed_rounds', 0) + 1 if not healthy else 0
    now = datetime.now(timezone.utc).isoformat()
    state = {'checked_at': now, 'total': len(tags), 'healthy': len(healthy), 'selected': selected or current,
        'selected_name': next((node['name'] for node in meta['nodes'] if node['tag'] == (selected or current)), None),
        'all_failed_rounds': failures, 'alert': 'all_failed' if failures >= 2 else None,
        'unhealthy_names': [node['name'] for node in meta['nodes'] if node['tag'] not in healthy]}
    atomic_json(status_path, state, 0o644)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', default=str(STATE_ROOT/'metadata.json'))
    parser.add_argument('--status', default=str(STATUS))
    args = parser.parse_args()
    try:
        meta = json.loads(Path(args.metadata).read_text())
        state = asyncio.run(check(meta, args.status))
        print(json.dumps({key: state[key] for key in ('checked_at', 'total', 'healthy', 'selected_name', 'alert')}, ensure_ascii=False))
    except Exception as error:
        # A broken controller/monitor is an alert, never silently "healthy".
        atomic_json(args.status, {'checked_at': datetime.now(timezone.utc).isoformat(),
            'alert': 'monitor_error', 'error_type': type(error).__name__}, 0o644)
        print('US_PROXY_MONITOR_ERROR '+type(error).__name__)
        raise SystemExit(1)


if __name__ == '__main__': main()
