#!/usr/bin/env python3
"""One-time approved configuration migration, run under the release lock."""
from pathlib import Path
import os
import shutil
from dotenv import dotenv_values, set_key


def enable(env_file, marker):
    if marker.exists():
        print('EXA_CHANNEL_CONFIG status=already_applied')
        return
    values = dotenv_values(env_file)
    if not values.get('AGENT_REACH_EXA_API_KEY'):
        print('EXA_CHANNEL_CONFIG status=skipped reason=no_server_key')
        return
    platforms = [p.strip() for p in values.get('AGENT_REACH_PLATFORMS', 'web,github,rss').split(',') if p.strip()]
    if 'exa' not in platforms:
        backup = env_file.with_name('.env.before-exa-search-enable')
        if not backup.exists():
            shutil.copy2(env_file, backup)
            backup.chmod(0o600)
        original_mode = env_file.stat().st_mode & 0o777
        set_key(str(env_file), 'AGENT_REACH_PLATFORMS', ','.join([*platforms, 'exa']), quote_mode='never')
        os.chmod(env_file, original_mode)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('applied\n')
    print('EXA_CHANNEL_CONFIG status=enabled')


if __name__ == '__main__':
    enable(Path('/var/www/everydayai/backend/.env'),
           Path('/var/www/everydayai/.configuration-migrations/20261015-enable-exa-search'))
