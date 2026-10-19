#!/usr/bin/python3.11 -I
"""Fixed, read-only SSH diagnostic endpoint. Never emits raw journal messages."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

UNITS = ('everydayai-backend', 'everydayai-sync', 'everydayai-wecom',
         'everydayai-conversation-actor', 'nginx')
COMMAND = 'everydayai-diagnostics-v1'
EXCEPTIONS = set(('KeyError ValueError TypeError RuntimeError AttributeError IndexError '
                  'AssertionError ImportError ModuleNotFoundError TimeoutError ConnectionError '
                  'PermissionError FileNotFoundError OperationalError IntegrityError ProgrammingError '
                  'DataError InterfaceError CancelledError HTTPException ValidationError '
                  'RequestError ConnectError ReadTimeout RemoteProtocolError').split())
CATEGORIES = {
    'traceback': r'Traceback \(most recent call last\)',
    'database_error': r'(?i)(deadlock|database.*(?:error|failed)|psycopg|sqlalchemy|duplicate key|connection pool)',
    'timeout': r'(?i)(timed?\s*out|timeout)',
    'connection_error': r'(?i)(connection refused|connection reset|connection error|broken pipe)',
    'permission_error': r'(?i)(permission denied|forbidden|unauthorized)',
    'resource_error': r'(?i)(out of memory|no space left|too many open files|oom-kill)',
    'service_lifecycle': r'(?i)(started|stopped|failed to start|main process exited|scheduled restart job)',
    'error': r'(?i)\b(error|exception|critical|fatal|failed)\b',
}


def project_message(message):
    """Allowlist projection, not regex substitution of arbitrary production text."""
    if not isinstance(message, str):
        return {'categories': ['unclassified']}
    result = {'categories': [k for k, p in CATEGORIES.items() if re.search(p, message)] or ['unclassified']}
    errors = sorted({x for x in re.findall(r'\b([A-Za-z]+(?:Error|Exception))\b', message) if x in EXCEPTIONS})
    if errors:
        result['exception_types'] = errors
    frames = []
    for path, line in re.findall(r'File "/var/www/everydayai/((?:backend|deploy)/[A-Za-z0-9_./-]+\.py)", line ([0-9]{1,7})', message):
        if '..' not in Path(path).parts and len(path) <= 240:
            frames.append({'file': path, 'line': int(line)})
    if frames:
        result['frames'] = frames[:12]
    codes = sorted({int(x) for x in re.findall(r'(?:HTTP/[12](?:\.\d)?"?\s+|status[=: ]+)([1-5][0-9]{2})\b', message, re.I)})
    if codes:
        result['http_status'] = codes
    return result


def bounded_command(args, *, limit=2*1024*1024):
    # Raw content remains on the server in a private temporary file, then is deleted.
    with tempfile.TemporaryFile() as out:
        process = subprocess.run(args, stdout=out, stderr=subprocess.DEVNULL, timeout=10,
                                 env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
        out.seek(0)
        data = out.read(limit + 1)
    return process.returncode, data[:limit], len(data) > limit


def service_status(unit):
    code, data, truncated = bounded_command(['/usr/bin/systemctl', 'show', unit,
        '--property=ActiveState,SubState,MainPID,NRestarts,ExecMainStatus', '--no-pager'], limit=4096)
    if code or truncated:
        return {'query': 'failed'}
    fields = dict(line.split('=', 1) for line in data.decode(errors='replace').splitlines() if '=' in line)
    result = {'query': 'ok'}
    for key in ('ActiveState', 'SubState'):
        value = fields.get(key)
        result[key] = value if value in ('active','inactive','failed','activating','deactivating','running','dead','exited','auto-restart','start','stop','stop-sigterm','failed') else 'unknown'
    for key in ('MainPID', 'NRestarts', 'ExecMainStatus'):
        value = fields.get(key, '')
        if re.fullmatch(r'[0-9]{1,12}', value):
            result[key] = int(value)
    return result


def journal(unit):
    code, data, truncated = bounded_command(['/usr/bin/journalctl', '--unit='+unit,
        '--since=-15min', '--lines=100', '--no-pager', '--output=json',
        '--output-fields=__REALTIME_TIMESTAMP,PRIORITY,MESSAGE'])
    if code:
        return {'query': 'failed', 'records': []}
    records = []
    for raw in data.splitlines():
        try:
            entry = json.loads(raw)
            stamp = str(entry.get('__REALTIME_TIMESTAMP', ''))
            if not re.fullmatch(r'[0-9]{13,18}', stamp):
                continue
            record = {'time': datetime.datetime.fromtimestamp(int(stamp)/1000000,
                datetime.timezone.utc).isoformat(), **project_message(entry.get('MESSAGE'))}
            priority = str(entry.get('PRIORITY', ''))
            if re.fullmatch('[0-7]', priority):
                record['priority'] = int(priority)
            records.append(record)
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    return {'query': 'ok', 'truncated': truncated, 'records': records}


def collect():
    result = {'protocol': 1, 'collected_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'window_minutes': 15, 'max_records_per_service': 100,
              'gateway_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'raw_messages_included': False, 'database_accessed': False, 'deployed': False,
              'services': {}}
    for unit in UNITS:
        try:
            result['services'][unit] = {'status': service_status(unit), 'journal': journal(unit)}
        except (OSError, subprocess.TimeoutExpired):
            result['services'][unit] = {'query': 'failed'}
    return result


def main():
    if os.environ.get('SSH_ORIGINAL_COMMAND') != COMMAND:
        raise ValueError('Denied')
    print(json.dumps(collect(), separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError):
        raise SystemExit('DIAGNOSTICS_DENIED')
