#!/usr/bin/env python3
"""Explicit, lock-bound knowledge activation. Never touches business rows or ACLs."""
import argparse
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path('/var/www/everydayai')
RELEASE_URL = 'https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-8.noarch.rpm'
RELEASE_SHA256 = '4077577b4b7ff1314da2489d7a82e6af938ac4c106af305018e470adb4e9bf16'


def check_lock(token):
    if not token or Path(str(ROOT)+'.release-lock/owner').read_text().strip() != token:
        raise ValueError('KNOWLEDGE_RELEASE_LOCK_REQUIRED')


def install():
    if shutil.which('dnf'):
        # Alibaba Cloud Linux 3 is EL8 compatible. Other major versions must be reviewed.
        os_release = Path('/etc/os-release').read_text()
        if 'ID="alinux"' not in os_release or 'VERSION_ID="3"' not in os_release:
            raise ValueError('KNOWLEDGE_UNSUPPORTED_OS')
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            with tempfile.TemporaryDirectory(prefix='kb-deps-') as directory:
                path = Path(directory)/'rpmfusion-free-release-8.noarch.rpm'
                with urllib.request.urlopen(RELEASE_URL, timeout=60) as response:
                    data = response.read(1024*1024)
                if sha256(data).hexdigest() != RELEASE_SHA256:
                    raise ValueError('KNOWLEDGE_REPOSITORY_CHECKSUM_MISMATCH')
                path.write_bytes(data)
                subprocess.run(['dnf','-y','install',str(path)],check=True,timeout=300)
        subprocess.run(['dnf','-y','install','ffmpeg','poppler-utils'],check=True,timeout=600)
    else:
        raise ValueError('KNOWLEDGE_UNSUPPORTED_PACKAGE_MANAGER')
    for name in ('ffmpeg','ffprobe','pdftoppm'):
        subprocess.run([name,'-version' if name != 'pdftoppm' else '-v'],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--lock-token',required=True)
    args=parser.parse_args()
    check_lock(args.lock_token)
    install()
    check_lock(args.lock_token)
    import sys
    sys.path.insert(0,str(ROOT/'backend'))
    os.chdir(ROOT/'backend')
    from core.config import get_settings
    settings=get_settings()
    if not settings.dashscope_api_key or not settings.knowledge_workspace_enabled:
        raise ValueError('KNOWLEDGE_MODEL_OR_WORKSPACE_NOT_CONFIGURED')
    spec=importlib.util.spec_from_file_location('env_patch',ROOT/'deploy/env-patch.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.apply_patch_file(ROOT/'backend/.env',{
        'KNOWLEDGE_DERIVATION_ENABLED':'true',
        'KNOWLEDGE_MEDIA_ENABLED':'true',
        'KNOWLEDGE_SEMANTIC_ENABLED':'true',
    })
    print('KNOWLEDGE_ACTIVATION status=enabled historical_backfill=worker live_acl=required manual_content=preserved')


if __name__=='__main__':main()
