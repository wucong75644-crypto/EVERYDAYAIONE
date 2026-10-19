#!/usr/bin/python3.11 -I
"""Fixed, read-only SSH diagnostic endpoint. Never emits raw journal messages."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import selectors
import pwd
import stat
import time
from collections import Counter
from urllib.parse import urlsplit

UNITS = ('everydayai-backend', 'everydayai-sync', 'everydayai-wecom',
         'everydayai-conversation-actor', 'nginx')
COMMAND = 'everydayai-diagnostics-v1'
APP_ROOT = Path('/var/www/everydayai')
SCAN_LIMIT = 5000
BYTE_LIMIT = 2 * 1024 * 1024
NGINX_FILES = ('/var/log/nginx/access.log', '/var/log/nginx/error.log',
               '/var/log/nginx/everydayai-access.log', '/var/log/nginx/everydayai-error.log')
EXCEPTIONS = set(('KeyError ValueError TypeError RuntimeError AttributeError IndexError '
                  'AssertionError ImportError ModuleNotFoundError TimeoutError ConnectionError '
                  'PermissionError FileNotFoundError OperationalError IntegrityError ProgrammingError '
                  'DataError InterfaceError CancelledError HTTPException ValidationError '
                  'RequestError ConnectError ReadTimeout WriteTimeout PoolTimeout ConnectTimeout HTTPStatusError RemoteProtocolError').split())
CATEGORIES = {
    'traceback': r'Traceback \(most recent call last\)',
    'database_error': r'(?i)(deadlock|database.*(?:error|failed)|psycopg|sqlalchemy|duplicate key|connection pool)',
    'timeout': r'(?i)(timed?\s*out|timeout)',
    'connection_error': r'(?i)(connection refused|connection reset|connection error|broken pipe)',
    'permission_error': r'(?i)(permission denied|forbidden|unauthorized)',
    'resource_error': r'(?i)(out of memory|no space left|too many open files|oom-kill)',
    'service_lifecycle': r'(?i)(started|stopped|failed to start|main process exited|scheduled restart job)',
    'rate_limit': r'(?i)(\b429\b|rate.?limit|too many requests|限流)',
    'pagination_limit': r'(?i)(fetch_all_pages hit limit|分页.*上限)',
    'retry': r'(?i)(retry|backoff|重试|退避)',
    'upstream_timeout': r'upstream timed out',
    'upstream_connection_refused': r'connect\(\) failed.*while connecting to upstream',
    'upstream_closed': r'upstream prematurely closed connection',
    'upstream_invalid_response': r'upstream sent (?:invalid|too big)',
    'client_disconnect': r'client (?:prematurely closed|closed) connection',
    'tls_error': r'(?:SSL_do_handshake\(\) failed|SSL_read\(\) failed)',
    'request_limited': r'limiting requests',
    'missing_file': r'open\(\).*failed.*No such file',
    'error': r'(?i)(\b(error|exception|critical|fatal|failed)\b|错误|异常|失败)',
}


def code_location(module, line):
    # Only return a source path that actually exists in the deployed backend tree.
    if not re.fullmatch(r'(?:services|core|api|routers|routes|workers|main)(?:\.[A-Za-z_][A-Za-z0-9_]*)*', module):
        return None
    relative = Path('backend', *module.split('.')).with_suffix('.py')
    candidate = APP_ROOT.resolve() / relative
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.resolve() != candidate.absolute():
            return None
    except OSError:
        return None
    return {'file': str(relative), 'line': int(line)}


def project_message(message):
    """Only fixed enums, verified source locations and bounded numeric fields escape."""
    if not isinstance(message, str):
        return {'categories': ['unclassified']}
    message = re.sub(r'\x1b\[[0-9;]*m', '', message)
    result = {}
    header = re.match(r'^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*\|\s*'
                      r'(TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)\s*\|\s*'
                      r'([A-Za-z0-9_.]+):[A-Za-z0-9_]+:([0-9]{1,7})\s*[-|]\s*', message)
    body = message
    if header:
        result['level'] = header[1]
        location = code_location(header[2], header[3])
        if location:
            result['source'] = location
        body = message[header.end():]
    if not header:
        standard = re.match(r'^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s+', message)
        if standard:
            result['level'] = standard[1]
            body = message[standard.end():]
    access = re.search(r' - "(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) ([^"\r\n]*) HTTP/[0-9.]+" ([1-5][0-9]{2})(?: |$)', body)
    if access:
        status = int(access[3])
        result.update(component='http_access', method=access[1], route_group=route_group(access[2]),
            http_status=[status], categories=['http_server_error' if status >= 500 else
                'http_client_error' if status >= 400 else 'http_request'])
        return result
    categories = [k for k, pattern in CATEGORIES.items() if re.search(pattern, body)]
    if not categories:
        categories = ['routine'] if result.get('level') in ('TRACE', 'DEBUG', 'INFO', 'SUCCESS') else ['unclassified']
    if result.get('level') in ('ERROR', 'CRITICAL') and 'error' not in categories:
        categories.append('error')
    if result.get('level') == 'WARNING' and 'warning' not in categories:
        categories.append('warning')
    result['categories'] = categories
    errors = sorted({x for x in re.findall(r'\b([A-Za-z]+(?:Error|Exception|Timeout))\b', body) if x in EXCEPTIONS})
    if errors:
        result['exception_types'] = errors
    frames = []
    for path, line in re.findall(r'File "/var/www/everydayai/((?:backend|deploy)/[A-Za-z0-9_./-]+\.py)", line ([0-9]{1,7})', body):
        candidate = APP_ROOT.resolve() / path
        if ('..' not in Path(path).parts and len(path) <= 240
                and candidate.is_file() and not candidate.is_symlink()
                and candidate.resolve() == candidate.absolute()):
            frames.append({'file': path, 'line': int(line)})
    if frames:
        result['frames'] = frames[:12]
    codes = sorted({int(x) for x in re.findall(r'(?:HTTP/[12](?:\.\d)?"?\s+|status(?:_code)?[=: ]+)([1-5][0-9]{2})\b', body, re.I)})
    if codes:
        result['http_status'] = codes
    return result


def bounded_command(args, *, limit=BYTE_LIMIT, line_limit=None):
    # Read at most the byte budget into memory; never spool unbounded raw journals to disk.
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
    chunks = bytearray()
    truncated = False
    deadline = time.monotonic() + 10
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, 10)
                if not selector.select(remaining):
                    raise subprocess.TimeoutExpired(args, 10)
                data = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(chunks)))
                if not data:
                    break
                chunks.extend(data)
                if len(chunks) > limit or (line_limit is not None and chunks.count(b'\n') >= line_limit):
                    truncated = True
                    process.kill()
                    break
        process.wait(timeout=max(0.01, deadline-time.monotonic()))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
    data = bytes(chunks[:limit])
    if line_limit is not None and data.count(b'\n') >= line_limit:
        data = b'\n'.join(data.splitlines()[:line_limit])+b'\n'
    return process.returncode, data, truncated


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


def summarize(records, *, scanned, truncated, parse_errors=0):
    # Error/warning records survive routine traffic; all selections remain time-bounded.
    def important(record):
        return (record.get('level') in ('WARNING', 'ERROR', 'CRITICAL')
                or record.get('priority', 7) <= 4
                or any(c in ('traceback', 'database_error', 'timeout', 'connection_error', 'permission_error',
                    'resource_error', 'rate_limit', 'pagination_limit', 'retry', 'error', 'warning',
                    'upstream_timeout', 'upstream_connection_refused', 'upstream_closed',
                    'upstream_invalid_response', 'tls_error', 'request_limited', 'slow_request',
                    'nginx_error_unspecified') for c in record['categories'])
                or any(status >= 400 for status in record.get('http_status', [])))
    urgent = [r for r in records if important(r)]
    context = [r for r in records if not important(r)]
    def error_rank(record):
        return 0 if (record.get('level') in ('ERROR','CRITICAL')
            or record.get('exception_types') or record.get('frames')
            or any(c in ('error','traceback','database_error','resource_error', 'upstream_timeout',
                         'upstream_connection_refused','upstream_closed','upstream_invalid_response',
                         'tls_error','http_server_error') for c in record['categories'])
            or any(status >= 500 for status in record.get('http_status', []))) else 1
    urgent.sort(key=error_rank)  # stable: newest errors, then newest warnings, then context
    selected = (urgent + context)[:100]
    selected.sort(key=lambda r: r['time'], reverse=True)
    times = sorted(r['time'] for r in records)
    return {'query': 'ok', 'scan_limit': SCAN_LIMIT, 'scanned': scanned,
            'oldest_record_at': times[0] if times else None,
            'newest_record_at': times[-1] if times else None,
            'parsing_complete': parse_errors == 0,
            'scan_truncated': truncated, 'parse_errors': parse_errors,
            'records_omitted': max(0, len(records)-len(selected)),
            'important_records_omitted': max(0, len(urgent)-100),
            'selection': 'errors_then_warnings_then_latest',
            'category_counts': dict(Counter(c for r in records for c in r['categories'])),
            'level_counts': dict(Counter(r.get('level', 'UNKNOWN') for r in records)),
            'records': selected}


def journal(unit):
    code, data, truncated = bounded_command(['/usr/bin/journalctl', '--unit='+unit,
        '--since=-15min', '--no-tail', '--reverse', '--no-pager', '--output=json',
        '--output-fields=__REALTIME_TIMESTAMP,PRIORITY,MESSAGE'], line_limit=SCAN_LIMIT+1)
    if code and not truncated:
        return {'query': 'failed', 'records': []}
    # The byte cap may cut the last JSON line; report truncation without parsing that fragment.
    if truncated and not data.endswith(b'\n'):
        data = data.rpartition(b'\n')[0]
    lines = data.splitlines()
    truncated = truncated or len(lines) > SCAN_LIMIT
    records = []
    failures = 0
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now-datetime.timedelta(minutes=15)
    for raw in lines[:SCAN_LIMIT]:
        try:
            entry = json.loads(raw)
            stamp = str(entry.get('__REALTIME_TIMESTAMP', ''))
            if not re.fullmatch(r'[0-9]{13,18}', stamp):
                raise ValueError('timestamp')
            timestamp = datetime.datetime.fromtimestamp(int(stamp)/1000000, datetime.timezone.utc)
            if timestamp < cutoff or timestamp > now+datetime.timedelta(seconds=60):
                continue
            record = {'time': timestamp.isoformat(), **project_message(entry.get('MESSAGE'))}
            priority = str(entry.get('PRIORITY', ''))
            if re.fullmatch('[0-7]', priority):
                record['priority'] = int(priority)
            records.append(record)
        except (ValueError, TypeError, OverflowError, OSError):
            failures += 1
    return summarize(records, scanned=min(len(lines), SCAN_LIMIT), truncated=truncated, parse_errors=failures)


def route_group(target):
    # Never emit a URL/path segment supplied by a user.
    path = urlsplit(target).path
    for prefix, group in (('/health', 'health'), ('/api/auth', 'auth'), ('/api/chat', 'chat'),
                          ('/api/conversations', 'conversations'), ('/api/images', 'images'),
                          ('/api/files', 'files'), ('/api/wecom', 'wecom'), ('/api/kuaimai', 'kuaimai')):
        if path == prefix or path.startswith(prefix+'/'):
            return group
    return 'api_other' if path.startswith('/api/') else 'other'


def nginx_line(line, kind):
    if kind == 'access':
        match = re.match(r'^\S+ (?:\S+ \S+ )?\[([^]]+)\] "((?:\\.|[^"\\])*)" ([1-5][0-9]{2}) (?:[0-9]+|-)(.*)$', line)
        if not match:
            return None
        stamp = datetime.datetime.strptime(match[1], '%d/%b/%Y:%H:%M:%S %z')
        status = int(match[3])
        request = re.fullmatch(r'(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) ([^\r\n]*) HTTP/[0-9.]+', match[2])
        result = {'time': stamp.isoformat(), 'method': request[1] if request else 'UNKNOWN',
                  'route_group': route_group(request[2]) if request else 'unknown',
                  'http_status': [status], 'categories': ['http_server_error' if status >= 500 else
                      'http_client_error' if status >= 400 else 'http_request']}
        # Safe format has an unquoted numeric duration immediately after bytes. Combined does not.
        duration = re.fullmatch(r'\s+([0-9]{1,6}(?:\.[0-9]{1,6})?)\s*', match[4])
        if duration:
            result['duration_ms'] = round(float(duration[1])*1000)
            if result['duration_ms'] >= 5000:
                result['categories'].append('slow_request')
        return result
    match = re.match(r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(debug|info|notice|warn|error|crit|alert|emerg)\]', line)
    if not match:
        return None
    stamp = datetime.datetime.strptime(match[1], '%Y/%m/%d %H:%M:%S').astimezone()
    result = project_message(line[match.end():])
    result.update(time=stamp.isoformat(), level={'warn':'WARNING','error':'ERROR', 'crit':'CRITICAL',
        'alert':'CRITICAL','emerg':'CRITICAL','debug':'DEBUG'}.get(match[2], 'INFO'))
    if result['categories'] == ['unclassified']:
        result['categories'] = ['nginx_error_unspecified']
    return result


def nginx_file(path):
    # Fixed paths only, no symlinks, directories, devices, pipes or unbounded reads.
    if str(path) not in NGINX_FILES:
        raise ValueError('Denied')
    kind = 'access' if 'access' in str(path) else 'error'
    fd = None
    directory_fd = None
    try:
        try:
            nginx_uid = pwd.getpwnam('nginx').pw_uid
        except KeyError:
            nginx_uid = 0
        # Walk every component by descriptor: a concurrent rename/symlink cannot escape the fixed directory.
        directory_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in ('var', 'log', 'nginx'):
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            info = os.fstat(directory_fd)
            allowed_owners = (0, nginx_uid) if component == 'nginx' else (0,)
            if (info.st_uid not in allowed_owners or info.st_mode & 0o002
                    or (info.st_mode & 0o020 and info.st_gid != 0)):
                raise ValueError('unsafe parent')
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, nginx_uid)
                or info.st_mode & 0o002 or (info.st_mode & 0o020 and info.st_gid != 0)):
            raise ValueError('unsafe file')
        offset = max(0, info.st_size-BYTE_LIMIT)
        os.lseek(fd, offset, os.SEEK_SET)
        data = os.read(fd, BYTE_LIMIT)
        if offset:
            data = data.partition(b'\n')[2]
        lines = data.decode(errors='replace').splitlines()
        truncated = bool(offset) or len(lines) > SCAN_LIMIT
        records = []
        failures = 0
        cutoff = datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=15)
        for line in reversed(lines[-SCAN_LIMIT:]):
            try:
                record = nginx_line(line, kind)
                if record is None:
                    failures += 1
                elif cutoff <= datetime.datetime.fromisoformat(record['time']) <= cutoff+datetime.timedelta(minutes=16):
                    records.append(record)
            except (ValueError, OverflowError):
                failures += 1
        result = summarize(records, scanned=min(len(lines), SCAN_LIMIT), truncated=truncated, parse_errors=failures)
        result['kind'] = kind
        return result
    except FileNotFoundError:
        return {'query': 'absent', 'kind': kind, 'records': []}
    except (OSError, ValueError):
        return {'query': 'failed', 'kind': kind, 'records': []}
    finally:
        if fd is not None:
            os.close(fd)
        if directory_fd is not None:
            os.close(directory_fd)


def collect():
    result = {'protocol': 1, 'collected_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'window_minutes': 15, 'max_records_per_service': 100, 'scan_limit': SCAN_LIMIT,
              'selection': 'errors_then_warnings_then_latest',
              'gateway_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'raw_messages_included': False, 'database_accessed': False, 'deployed': False,
              'services': {}}
    for unit in UNITS:
        try:
            result['services'][unit] = {'status': service_status(unit), 'journal': journal(unit)}
        except (OSError, subprocess.TimeoutExpired):
            result['services'][unit] = {'query': 'failed'}
    result['nginx_files'] = {path: nginx_file(path) for path in NGINX_FILES}
    # Read only the fixed release marker, never application .env or database state.
    try:
        marker = APP_ROOT / '.release-provenance'
        if not marker.is_symlink():
            with marker.open('rb') as stream:
                data = stream.read(4096)
            for line in data.decode(errors='replace').splitlines():
                match = re.fullmatch(r'commit=([0-9a-f]{40})', line)
                if match:
                    result['application_commit'] = match[1]
                    break
    except OSError:
        pass
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
