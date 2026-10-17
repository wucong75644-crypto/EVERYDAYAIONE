#!/usr/bin/env python3
"""Reuse a reviewed, pinned pending patch on a clean matching task base only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def reuse(root,patch,manifest,patch_sha,manifest_sha):
    root=root.resolve();data=patch.read_bytes();metadata=manifest.read_bytes()
    if hashlib.sha256(data).hexdigest()!=patch_sha:raise RuntimeError('Pending patch checksum mismatch')
    if hashlib.sha256(metadata).hexdigest()!=manifest_sha:raise RuntimeError('Pending manifest checksum mismatch')
    spec=json.loads(metadata)
    if spec['patch_sha256']!=patch_sha:raise RuntimeError('Manifest/patch identity mismatch')
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    if Path(git('rev-parse','--show-toplevel')).resolve()!=root:raise RuntimeError('Expected task checkout root')
    if not git('branch','--show-current').startswith('codex/task/'):raise RuntimeError('Only codex/task/* branches are supported')
    forbidden={'.git','.venv','node_modules','.cloud-dev','logs','instances'}
    for name in spec['files']:
        path=Path(name)
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',name) or path.is_absolute() or '..' in path.parts:
            raise RuntimeError('Unsafe patch path')
        if any(p in forbidden or p.startswith('.env') for p in path.parts):raise RuntimeError('Private data must not be patched')
    paths={line.split('\t',2)[2] for line in git('apply','--numstat',str(patch)).splitlines()}
    if paths!=set(spec['files']):raise RuntimeError('Patch file list differs from the reviewed manifest')
    def matches():
        return all((root/name).is_file() and not (root/name).is_symlink() and hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in spec['files'].items())
    if matches():
        print('Reviewed repair already present; no files changed');return
    if git('rev-parse','HEAD')!=spec['base_commit']:raise RuntimeError('Refusing to apply repair to a different base')
    if git('status','--porcelain','--untracked-files=normal'):raise RuntimeError('Preserved existing changes: pending patch requires a clean base')
    subprocess.run(['git','-C',str(root),'apply','--check','--whitespace=nowarn',str(patch)],check=True)
    subprocess.run(['git','-C',str(root),'apply','--whitespace=nowarn',str(patch)],check=True)
    if not matches():raise RuntimeError('Patched files differ from the reviewed file checksums')
    print('Reviewed repair applied without staging, committing or pushing')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--patch',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--patch-sha',required=True);p.add_argument('--manifest-sha',required=True)
    a=p.parse_args();reuse(a.root,a.patch,a.manifest,a.patch_sha,a.manifest_sha)
