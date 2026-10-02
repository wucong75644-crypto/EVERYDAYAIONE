"""Fail before deployment writes when .env targets a different database."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from dotenv import dotenv_values


def verify_identity(connection, expected):
    actual = connection.execute('SELECT current_database(), oid FROM pg_database '
                                'WHERE datname=current_database()').fetchone()
    if (not isinstance(expected, dict) or actual !=
            (expected.get('database'), expected.get('database_oid'))):
        raise ValueError('PRODUCTION_DATABASE_IDENTITY_MISMATCH')


def main():
    root = Path('/var/www/everydayai')
    expected = json.loads((root / '.production-database-identity.json').read_text())
    values = dotenv_values(root / 'backend/.env')
    with psycopg.connect(values['DATABASE_URL'], connect_timeout=10) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        verify_identity(connection, expected)
        required = ['users', 'conversations', 'messages', 'error_logs']
        if values.get('SKILL_CATALOG_ENABLED', '').lower() == 'true':
            required += ['skill_packages', 'skill_revisions', 'skill_assignments', 'skill_drafts']
        for table in required:
            if not connection.execute('SELECT to_regclass(%s) IS NOT NULL', ('public.' + table,)).fetchone()[0]:
                raise ValueError('PRODUCTION_DATABASE_SCHEMA_MISSING')
        connection.rollback()
    print('Production database identity and required schema verified')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Production database verification failed; deployment stopped')
