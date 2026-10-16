#!/usr/bin/env python3
"""Install the tested XHS candidate; invoked only by the controlled deploy executor."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
from uuid import UUID
from dotenv import dotenv_values

ROOT = Path('/opt/everydayai-xhs-a5c8f779')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    groups = json.loads(dotenv_values('/var/www/everydayai/backend/.env').get('AGENT_REACH_XHS_SLOTS_JSON') or '{}')
    slots = [slot for entries in groups.values() for slot in entries]
    if not slots:
        print('XHS_LOGIN_FIX status=skipped reason=no_slots')
        return
    assets = Path(__file__).resolve().parent
    manifest = json.loads((assets/'login-fix-artifact.json').read_text())
    if digest(assets/'login-browser.patch') != manifest['patch_sha256']:
        raise RuntimeError('XHS patch does not match verified artifact')
    for name, expected in manifest.get('assets_sha256', {}).items():
        if name not in {'search_readiness.go', 'search_readiness_test.go', 'commerce.go', 'commerce_test.go', 'commerce.patch'} or digest(assets/name) != expected:
            raise RuntimeError('XHS search assets do not match verified artifact')
    target = ROOT/'xiaohongshu-mcp'
    if digest(target) == manifest['sha256']:
        print('XHS_LOGIN_FIX status=already_installed')
        return
    candidate_directory = manifest.get('candidate_directory', 'login-fix-candidate')
    if candidate_directory not in {'login-fix-candidate', 'search-fix-candidate', 'commerce-fix-candidate', 'query-timeout-candidate'}:
        raise RuntimeError('Invalid XHS candidate directory')
    candidate = ROOT/candidate_directory/'xiaohongshu-mcp'
    if digest(candidate) != manifest['sha256']:
        raise RuntimeError('Verified XHS candidate unavailable; stop without replacing service')
    units = sorted({'everydayai-reach-xhs-'+str(UUID(slot['connection_id']))+'.service' for slot in slots})
    for unit in units:
        subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True)
    backup = ROOT/('xiaohongshu-mcp.backup-'+digest(target))
    if not backup.exists():
        shutil.copy2(target, backup)
    staged = ROOT/'xiaohongshu-mcp.installing'
    shutil.copyfile(candidate, staged)
    staged.chmod(0o755)
    os.replace(staged, target)
    try:
        subprocess.run(['systemctl', 'restart', *units], check=True)
        for unit in units:
            subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True)
        for slot in slots:
            origin = slot['origin']
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1','::1'} or parsed.path not in {'','/'} or parsed.query or parsed.fragment or parsed.username:
                raise RuntimeError('Invalid dedicated XHS origin')
            # Local readiness only; does not create a QR or access platform data.
            for attempt in range(10):
                try:
                    with urllib.request.urlopen(origin.rstrip('/')+'/health', timeout=2) as response:
                        if response.status != 200:
                            raise RuntimeError('XHS readiness failed')
                    break
                except OSError:
                    if attempt == 9:
                        raise
                    time.sleep(1)
    except BaseException:
        shutil.copyfile(backup, staged)
        staged.chmod(0o755)
        os.replace(staged, target)
        subprocess.run(['systemctl', 'restart', *units], check=False)
        raise
    print('XHS_LOGIN_FIX status=installed services='+str(len(units))+' sha256='+manifest['sha256'])

if __name__ == '__main__':
    main()
