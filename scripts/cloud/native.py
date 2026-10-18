#!/usr/bin/env python3
"""Rootless native dev tools, authenticated pinned artifacts, no system services."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from urllib.request import urlopen

BASE=Path('/workspace/.everydayaione-dev')


def ensure():
    if platform.machine()!='x86_64':raise RuntimeError('Pinned Debian native packages require amd64')
    BASE.mkdir(mode=0o700,exist_ok=True);os.chmod(BASE,0o700)
    packages=BASE/'packages';packages.mkdir(exist_ok=True)
    with (BASE/'native-install.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        manifest=json.loads(Path(__file__).with_name('native-packages.json').read_text())
        for package in manifest['packages']:
            path=packages/package['file']
            if not path.exists():
                temp=path.with_suffix('.partial')
                # Inherit session HTTPS proxy and CA trust. Never disable TLS.
                with urlopen(package['url'],timeout=60) as source,temp.open('wb') as dest:
                    while data:=source.read(1024*1024):dest.write(data)
                temp.replace(path)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=package['sha256']:
                raise RuntimeError('Native package integrity failure: '+package['package'])
        runtime=BASE/'runtime'
        stamp=runtime/'cloud-native-manifest.sha256'
        digest=hashlib.sha256(Path(__file__).with_name('native-packages.json').read_bytes()).hexdigest()
        required=[runtime/'usr/lib/postgresql/17/bin/postgres',runtime/'usr/lib/postgresql/17/lib/vector.so',runtime/'usr/bin/redis-server']
        if not stamp.exists() or stamp.read_text()!=digest or not all(p.exists() for p in required):
            for package in manifest['packages']:
                subprocess.run(['dpkg-deb','-x',str(packages/package['file']),str(runtime)],check=True)
            stamp.write_text(digest)
        if not all(p.exists() for p in required):raise RuntimeError('Native runtime incomplete')
    print('Verified native PostgreSQL/pgvector and Redis tools ready')


if __name__=='__main__':ensure()
