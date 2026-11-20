#!/usr/bin/env python3
"""Provision the personal MCP credential during the controlled release only."""
import argparse
import importlib.util
from pathlib import Path
import secrets

from dotenv import dotenv_values


def configure(root: Path, lock_token: str) -> str:
    owner_file = Path(str(root) + '.release-lock') / 'owner'
    if not lock_token or owner_file.read_text().strip() != lock_token:
        raise ValueError('KIE_MCP_RELEASE_LOCK_REQUIRED')
    env_file = root / 'backend/.env'
    values = dotenv_values(env_file, interpolate=False)
    if not values.get('KIE_API_KEY'):
        return 'skipped_no_kie_key'
    existing = values.get('KIE_MCP_TOKEN')
    if existing:
        if len(existing) < 32:
            raise ValueError('KIE_MCP_TOKEN_TOO_SHORT')
        return 'already_configured'
    spec = importlib.util.spec_from_file_location('release_env_patch', Path(__file__).with_name('env-patch.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.apply_patch_file(env_file, {'KIE_MCP_TOKEN': secrets.token_urlsafe(48)})
    return 'configured'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lock-token', required=True)
    args = parser.parse_args()
    try:
        status = configure(Path('/var/www/everydayai'), args.lock_token)
    except Exception as error:
        code = str(error)
        if not code.startswith('KIE_MCP_'):
            code = 'KIE_MCP_CONFIGURATION_FAILED'
        raise SystemExit(code)
    print('KIE_MCP_CONFIG status=' + status)
