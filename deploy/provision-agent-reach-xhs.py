#!/usr/bin/env python3
"""Provision dedicated localhost XHS services for explicit organization UUIDs.

Run with the backend venv on the server. Defaults to a read-only plan. This does
not log in, restart the application, or enable Agent Reach.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from uuid import UUID, uuid4


def atomic_write(path, text):
    fd, name = tempfile.mkstemp(prefix='.reach-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        if path.exists():
            stat = path.stat()
            os.fchown(fd, stat.st_uid, stat.st_gid)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org', action='append', required=True)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--env-file', type=Path, default=Path('/var/www/everydayai/backend/.env'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    orgs = sorted({str(UUID(org)) for org in args.org})
    from dotenv import dotenv_values
    values = dotenv_values(args.env_file)
    groups = json.loads(values.get('AGENT_REACH_XHS_SLOTS_JSON') or '{}')
    if not isinstance(groups, dict):
        raise ValueError('Invalid slot mapping')
    missing = [org for org in orgs if not groups.get(org)]
    print(json.dumps({'organizations':len(orgs), 'existing':len(orgs)-len(missing),
        'to_provision':len(missing), 'mode':'apply' if args.apply else 'plan'}))
    if not args.apply or not missing:
        return
    if os.geteuid() != 0 or not args.binary.is_absolute() or not os.access(args.binary, os.X_OK):
        raise ValueError('Root and an executable absolute binary path are required')
    if not args.cache.is_absolute() or not args.cache.is_dir():
        raise ValueError('Preverified browser cache is required')
    lock = open('/var/lock/everydayai-reach-provision.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if Path('/var/www/everydayai.release-lock').exists():
        raise ValueError('Production release is in progress; provisioning stopped')
    # Dedicated directories also isolate cookies, fingerprint seed and HOME.
    root = Path('/var/lib/everydayai/reach-xhs')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    media = Path(values.get('AGENT_REACH_MEDIA_STAGING_DIR') or '/var/lib/everydayai/reach-media')
    if not media.is_absolute() or media.is_symlink():
        raise ValueError('Invalid media staging directory')
    media.mkdir(parents=True, exist_ok=True, mode=0o700)
    if media.stat().st_uid != 0 or media.stat().st_mode & 0o077:
        raise ValueError('Media directory must be private to the service account')
    # Unit files interpolate only validated paths and generated values.
    for path in (args.binary, args.cache, media):
        if any(ch in str(path) for ch in '\n\r\t %"'):
            raise ValueError('Unsupported service path')
    units = []
    held = []
    try:
        used = {entry['origin'].rstrip('/') for entries in groups.values() for entry in entries}
        used.update(json.loads(values.get('AGENT_REACH_XHS_ORIGINS_JSON') or '{}').values())
        for org in missing:
            port = next((port for port in range(18061, 19061)
                if 'http://127.0.0.1:'+str(port) not in used and reserve(port, held)), None)
            if port is None:
                raise ValueError('No free localhost service port')
            origin = 'http://127.0.0.1:'+str(port)
            used.add(origin)
            cid = str(uuid4())
            token = secrets.token_urlsafe(48)
            home = root / cid
            home.mkdir(mode=0o700)
            atomic_write(home/'service.env', 'AUTH_TOKEN='+token+'\nCOOKIES_PATH='+str(home/'cookies.json')+'\n')
            unit = 'everydayai-reach-xhs-'+cid+'.service'
            unit_text = f'''[Unit]
Description=Dedicated organization XHS service
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=root
WorkingDirectory={home}
EnvironmentFile={home}/service.env
Environment=HOME={home}
Environment=XDG_CACHE_HOME={args.cache}
ExecStart={args.binary} -headless=true -port 127.0.0.1:{port}
Restart=on-failure
RestartSec=10
UMask=0077
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={home} {media}
MemoryMax=1G
TasksMax=256
TimeoutStopSec=30
[Install]
WantedBy=multi-user.target
'''
            atomic_write(Path('/etc/systemd/system')/unit, unit_text)
            groups[org] = [{'connection_id':cid, 'origin':origin, 'service_token':token}]
            units.append((unit, origin))
        for sock in held:
            sock.close()
        held.clear()
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        for unit, origin in units:
            subprocess.run(['systemctl', 'enable', '--now', unit], check=True, capture_output=True)
            for attempt in range(30):
                try:
                    with urllib.request.urlopen(origin+'/health', timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Dedicated service did not become healthy; app configuration unchanged')
            try:
                urllib.request.urlopen(origin+'/api/v1/login/status', timeout=2)
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise RuntimeError('Service authentication check failed') from None
            else:
                raise RuntimeError('Service did not reject unauthenticated access')
        original = args.env_file.read_text()
        backup = root / ('application-env-before-'+str(uuid4()))
        atomic_write(backup, original)
        updates = {'AGENT_REACH_XHS_SLOTS_JSON':json.dumps(groups, separators=(',', ':')),
            'AGENT_REACH_MEDIA_STAGING_DIR':str(media)}
        lines = [line for line in original.splitlines() if line.split('=',1)[0].strip() not in updates]
        lines += [key+'='+json.dumps(value) for key, value in updates.items()]
        atomic_write(args.env_file, '\n'.join(lines)+'\n')
        print(json.dumps({'provisioned':len(units), 'health':'passed', 'unauthenticated_access':'rejected',
            'application_restarted':False, 'platform_login':'not_verified'}))
    finally:
        for sock in held:
            sock.close()
        lock.close()


def reserve(port, held):
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', port))
    except OSError:
        sock.close()
        return False
    held.append(sock)
    return True


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Exceptions may originate from configuration parsing. Never print them.
        print('Provisioning failed; inspect service status and configuration locally. No secrets printed.')
        raise SystemExit(1)
