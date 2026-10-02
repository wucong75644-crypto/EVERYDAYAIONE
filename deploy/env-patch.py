"""Patch explicitly named environment keys, keeping all other production values."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from dotenv import dotenv_values, set_key


def validate_patch(patch):
    if not isinstance(patch, dict) or not patch:
        raise ValueError('ENV_EXPLICIT_KEYS_REQUIRED')
    for key, value in patch.items():
        if not isinstance(key, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError('ENV_INVALID_KEY')
        if key == 'DATABASE_URL':
            raise ValueError('ENV_DATABASE_CHANGE_REQUIRES_SEPARATE_RECOVERY')
        # EnvironmentFile and dotenv must agree on the serialized one-line
        # value. Reject quote/newline forms requiring incompatible parsers.
        if not isinstance(value, str) or any(c in value for c in ("'", '\r', '\n', '\0')):
            raise ValueError('ENV_INVALID_VALUE')


def build_patch(source, keys):
    if not keys:
        raise ValueError('ENV_EXPLICIT_KEYS_REQUIRED')
    values = dotenv_values(source, interpolate=False)
    if any(key not in values or values[key] is None for key in keys):
        raise ValueError('ENV_SELECTED_KEY_MISSING')
    patch = {key: values[key] for key in keys}
    validate_patch(patch)
    return patch


def apply_patch_file(target, patch):
    validate_patch(patch)
    target = Path(target)
    if target.is_symlink() or not target.is_file():
        raise ValueError('ENV_TARGET_MUST_BE_REGULAR_FILE')
    original = target.read_bytes()
    before = dotenv_values(target, interpolate=False)
    stat = target.stat()
    fd, name = tempfile.mkstemp(prefix='.env.patch-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(original)
        for key, value in patch.items():
            set_key(str(temporary), key, value, quote_mode='always')
        after = dotenv_values(temporary, interpolate=False)
        if any(after.get(k) != v for k, v in patch.items()):
            raise ValueError('ENV_PATCH_VALUE_MISMATCH')
        if any(after.get(k) != v for k, v in before.items() if k not in patch):
            raise ValueError('ENV_UNSELECTED_VALUE_CHANGED')
        if target.read_bytes() != original:
            raise ValueError('ENV_CONCURRENT_CHANGE')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = target.with_name('.env.backup.patch.' + stamp)
        # Create private BEFORE writing any secrets, independent of umask.
        backup_fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(backup_fd, 'wb') as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.chown(temporary, stat.st_uid, stat.st_gid)
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-from')
    parser.add_argument('--key', action='append', default=[])
    parser.add_argument('--lock-token')
    args = parser.parse_args()
    if args.build_from:
        print(json.dumps(build_patch(args.build_from, args.key)))
        return
    root = Path('/var/www/everydayai')
    owner = Path(str(root) + '.release-lock/owner')
    if not args.lock_token or owner.read_text().strip() != args.lock_token:
        raise ValueError('ENV_RELEASE_LOCK_REQUIRED')
    patch = json.load(sys.stdin)
    apply_patch_file(root / 'backend/.env', patch)
    print(json.dumps({'patched_keys': sorted(patch), 'production_config_preserved': True}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error)
        if not re.fullmatch(r'ENV_[A-Z_]+', code):
            code = 'ENV_PATCH_FAILED'
        raise SystemExit(code)
