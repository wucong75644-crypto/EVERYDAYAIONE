#!/usr/bin/env python3
"""Request a check of an immutable pushed task commit; never deploy."""
import argparse
from datetime import datetime, timezone
import hashlib
import re
import subprocess
from uuid import uuid4


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    branch = git('branch', '--show-current')
    if not branch.startswith('codex/task/'):
        raise RuntimeError('Run from the original codex/task/* checkout')
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('Commit and push the task files before requesting checks')
    sha = git('rev-parse', 'HEAD')
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise RuntimeError('Invalid commit')
    remote = git('ls-remote', '--exit-code', 'origin', 'refs/heads/' + branch).split()
    if not remote or remote[0] != sha:
        raise RuntimeError('Task HEAD must equal its remote branch tip')
    task = hashlib.sha256(branch.encode()).hexdigest()[:16]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    tag = f'preflight/{task}/{sha}/{stamp}-{uuid4().hex[:8]}'
    if not args.dry_run:
        subprocess.run(['git', 'push', 'origin', sha + ':refs/tags/' + tag], check=True)
    print(f'CHECK_REQUEST status={"dry-run" if args.dry_run else "requested"} commit={sha} ref={tag}')
    print('A successful request is not a passed check. Wait for both GitHub jobs for this exact SHA.')


if __name__ == '__main__':
    main()
