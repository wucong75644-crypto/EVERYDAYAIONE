"""Register historical completed image/video task outputs in the admin asset space.

Defaults to read-only: python scripts/backfill_generated_assets.py [--user-id UUID]
Use --apply explicitly to write idempotent task asset refs. No files are regenerated.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.database import get_db
from services.assets.asset_registry import register_task_media_best_effort


def task_parts(db, task):
    result = task.get('result_data')
    if isinstance(result, str):
        result = json.loads(result)
    if isinstance(result, dict):
        parts = [result]
    elif isinstance(result, list):
        parts = result
    else:
        message_id = task.get('placeholder_message_id') or task.get('assistant_message_id')
        if not message_id:
            return []
        message = db.table('messages').select('content').eq('id', message_id).maybe_single().execute().data
        parts = (message or {}).get('content') or []
        if isinstance(parts, str):
            parts = json.loads(parts)
        # Each batch task owns only its image, not every output in the message.
        if task.get('batch_id'):
            index = task.get('image_index', 0)
            parts = parts[index:index + 1]
    return [part for part in parts if isinstance(part, dict) and part.get('type') in ('image', 'video') and part.get('url') and not part.get('failed')]


def backfill(db, *, apply=False, user_id=None, limit=1000):
    stats = dict(scanned=0, missing=0, registered=0, failed=0)
    cursor = None
    while stats['scanned'] < limit:
        query = db.table('tasks').select('*').eq('status', 'completed').in_('type', ['image', 'video'])
        if user_id:
            query = query.eq('user_id', user_id)
        if cursor:
            query = query.gt('id', cursor)
        rows = query.order('id').limit(min(100, limit - stats['scanned'])).execute().data or []
        if not rows:
            break
        for task in rows:
            stats['scanned'] += 1
            parts = task_parts(db, task)
            if not parts:
                continue
            refs = db.table('user_asset_refs').select('ref_key').eq('source_task_id', task['id']).execute().data or []
            existing = {ref['ref_key'] for ref in refs}
            missing = sum(f"task:{task['id']}:{index}" not in existing for index in range(len(parts)))
            stats['missing'] += missing
            if apply and missing:
                registered = register_task_media_best_effort(db, task=task, content_parts=parts)
                stats['registered'] += len(registered)
                stats['failed'] += len(parts) - len(registered)
        cursor = rows[-1]['id']
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--user-id', type=UUID)
    parser.add_argument('--limit', type=int, default=1000)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    stats = backfill(get_db(), apply=args.apply, user_id=str(args.user_id) if args.user_id else None, limit=args.limit)
    print(json.dumps({'mode': 'apply' if args.apply else 'dry-run', **stats}))
    if stats['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
