#!/usr/bin/env python3
"""Read the fixed diagnostic endpoint. No deployment or application env access."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from delivery import config_text, verify_actor, verify_environment, write_ssh_file


def collection_complete(report):
    units = ('everydayai-backend', 'everydayai-sync', 'everydayai-wecom', 'everydayai-conversation-actor', 'nginx')
    services = report.get('services', {})
    if set(services) != set(units) or not all(
            services[unit].get('status', {}).get('query') == 'ok'
            and services[unit].get('journal', {}).get('query') == 'ok'
            and services[unit]['journal'].get('parsing_complete') is True for unit in units):
        return False
    files = report.get('nginx_files', {})
    required = ('/var/log/nginx/access.log', '/var/log/nginx/error.log',
                '/var/log/nginx/everydayai-access.log', '/var/log/nginx/everydayai-error.log')
    return (set(files) == set(required)
        and all(value.get('query') == 'absent' or (value.get('query') == 'ok'
                and value.get('parsing_complete') is True) for value in files.values())
        and all(any(value.get('kind') == kind and value.get('query') == 'ok' for value in files.values())
                for kind in ('access', 'error')))


def query():
    repository = os.environ['GITHUB_REPOSITORY']
    verify_actor(repository, os.environ['DELIVERY_APPROVER'])
    verify_environment(repository, 'production', os.environ['DELIVERY_APPROVER'])
    if not os.environ['GITHUB_REF'].startswith('refs/tags/delivery-verification/diagnostics-'):
        raise ValueError('Invalid request')
    config = dict((k, shlex.split(v)[0]) for k, v in
                  (line.split('=', 1) for line in config_text(os.environ['DELIVERY_CONFIG_JSON']).splitlines()))
    with tempfile.TemporaryDirectory(prefix='readonly-diagnostics-') as directory:
        root = Path(directory)
        write_ssh_file(root/'key', os.environ['DIAGNOSTICS_SSH_KEY'])
        write_ssh_file(root/'hosts', os.environ['DELIVERY_KNOWN_HOSTS'])
        command = ['ssh', '-F', '/dev/null', '-i', str(root/'key'), '-p', config['SERVER_PORT'],
                   '-o', 'UserKnownHostsFile='+str(root/'hosts'), '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', '-o', 'BatchMode=yes',
                   '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=15', '-T',
                   config['SERVER_USER']+'@'+config['SERVER_HOST']]
        clean = {k: v for k, v in os.environ.items() if not k.startswith(('DELIVERY_', 'DIAGNOSTICS_'))}
        result = subprocess.run(command+['everydayai-diagnostics-v1'], capture_output=True,
                                text=True, timeout=130, env=clean)
        if result.returncode or len(result.stdout) > 1024*1024:
            raise ValueError('SSH diagnostic request failed')
        report = json.loads(result.stdout)
        expected = hashlib.sha256((Path(__file__).parent/'diagnostics_gateway.py').read_bytes()).hexdigest()
        if (report.get('gateway_sha256') != expected or report.get('protocol') != 1
                or report.get('raw_messages_included') is not False
                or report.get('database_accessed') is not False or report.get('deployed') is not False):
            raise ValueError('Diagnostic identity mismatch')
        for forbidden in ('id', 'cat /etc/passwd', 'systemctl restart everydayai-backend',
                          'everydayai-diagnostics-v1; id'):
            denied = subprocess.run(command+[forbidden], capture_output=True, text=True, timeout=15, env=clean)
            if denied.returncode == 0 or 'DIAGNOSTICS_DENIED' not in denied.stderr:
                raise ValueError('Command restriction check failed')
        report['arbitrary_commands_denied'] = True
    report['collection_complete'] = collection_complete(report)
    # Only the hash-verified allowlist projection is emitted, never SSH stderr or raw journals.
    encoded = json.dumps(report, separators=(',', ':'))
    print('DIAGNOSTICS_RESULT '+encoded)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write('Read-only production diagnostics; no deployment, restart or database access.\n\n```json\n'+encoded+'\n```\n')
    if not report['collection_complete']:
        raise ValueError('Incomplete collection; see projected report')


if __name__ == '__main__':
    try:
        query()
    except Exception:
        raise SystemExit('DIAGNOSTICS_QUERY_FAILED: details suppressed; no deployment or restart attempted')
