"""Explicit, fail-closed recovery on the production management connection.

Invoked only by release.sh --recover-lock with an independently identified failed
commit. Never records successful provenance and never executes a migration.
"""
import argparse
import base64
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

APP = Path('/var/www/everydayai')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_owner(lock, expected):
    require(not lock.is_symlink() and lock.is_dir(), 'Release lock missing or unsafe')
    owner = lock / 'owner'
    require(not owner.is_symlink() and owner.read_text().strip() == expected,
            'Release owner changed; preserve the lock')


def active_writers():
    writers = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmd = (entry / 'cmdline').read_bytes().split(b'\0')
            exe = Path(cmd[0].decode()).name
            arguments = b' '.join(cmd[1:]).decode(errors='replace')
            # Normal application Python workers are not deployment writers.
            if exe in ('rsync', 'pip', 'pip3', 'psql') or (
                    exe in ('bash', 'sh', 'python', 'python3', 'python3.11') and
                    any(x in arguments for x in ('deploy/deploy.sh', 'deploy/release.sh',
                                                 'scheduled-task-drain.py', 'pip install'))):
                writers.append(int(entry.name))
            if (exe in ('bash', 'sh') and (not arguments.strip() or cmd[1:3] == [b'-s', b'--'])) or (exe.startswith('python') and cmd[1:2] == [b'-']):
                writers.append(int(entry.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError):
            continue
    return sorted(set(writers))


def validate_manifest(app, manifest, *, allow_absent=False):
    require(bool(manifest), 'Failed candidate source manifest is empty')
    for name, expected in manifest.items():
        path = Path(name)
        require(name.startswith(('backend/', 'deploy/')) and '..' not in path.parts
                and not path.is_absolute() and not any(p.startswith('.env') or p == 'config.env' for p in path.parts), 'Unsafe evidence path')
        target = app / path
        if allow_absent and expected is None:
            require(not target.exists() and not target.is_symlink()
                    and target.parent.resolve().is_relative_to(app.resolve()),
                    'Pre-mutation candidate file already exists: ' + name)
            continue
        require(not target.is_symlink() and target.is_file() and target.resolve().is_relative_to(app.resolve()), 'Failed candidate file missing or unsafe: ' + name)
        require(hashlib.sha256(target.read_bytes()).hexdigest() == expected,
                'Production differs from the identified failed candidate: ' + name)


def database_snapshot(app):
    import psycopg
    from dotenv import dotenv_values
    expected = json.loads((app / '.production-database-identity.json').read_text())
    with psycopg.connect(dotenv_values(app / 'backend/.env')['DATABASE_URL'], connect_timeout=10) as c:
        c.execute('SET TRANSACTION READ ONLY')
        identity = c.execute('SELECT current_database(), oid FROM pg_database WHERE datname=current_database()').fetchone()
        require(identity == (expected['database'], expected['database_oid']), 'Database identity mismatch')
        active = c.execute("SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() "
                           "AND state='active' AND (query ILIKE '%deployment_schema_migrations%' "
                           "OR query ~* '(CREATE|ALTER|DROP)[[:space:]]+(TABLE|FUNCTION|INDEX)')").fetchone()[0]
        require(active == 0, 'Database writer still active')
        ledger = c.execute('SELECT migration_path,checksum FROM public.deployment_schema_migrations ORDER BY migration_path').fetchall()
        c.rollback()
    return ledger


def recover(owner, failed_commit, branch, manifest, app=APP):
    require(os.geteuid() == 0, 'Recovery requires the existing management connection')
    require(re.fullmatch(r'[A-Za-z0-9-]+', owner), 'Invalid owner')
    require(re.fullmatch(r'[a-f0-9]{40}', failed_commit), 'Full failed commit required')
    require(re.fullmatch(r'codex/task/[A-Za-z0-9_.-]+', branch), 'Original task required')
    lock = Path(str(app) + '.release-lock')
    check_owner(lock, owner)
    attempt = lock / 'attempt'
    if attempt.exists():
        fields = dict(line.split('=', 1) for line in attempt.read_text().splitlines() if '=' in line)
        require(fields.get('commit') == failed_commit and fields.get('source') == branch,
                'Failed candidate does not match the recorded attempt')
    # Nonblocking checks: do not wait on a live migration, and keep both handles
    # through the atomic archival of the old lock.
    with open('/tmp/everydayai-release-recovery.lock', 'a') as gate, open('/tmp/everydayai-schema-migrations.lock', 'a') as migration:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(migration, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not active_writers(), 'Deployment writer still active')
        marker = app / '.release-provenance'
        pre_mutation = manifest.get('recovery_mode') == 'pre_mutation'
        proof = manifest if pre_mutation else None
        files = proof['files'] if proof else manifest
        if pre_mutation:
            require(attempt.exists(), 'Pre-mutation recovery requires the recorded attempt')
            require(re.fullmatch(r'[a-f0-9]{40}', proof.get('previous_commit', '')),
                    'Previous production commit missing')
            local_log = proof.get('local_log', {})
            require(re.fullmatch(r'[a-f0-9]{64}', local_log.get('sha256', ''))
                    and local_log.get('stages') in (['preflight'], ['preflight','tests_and_build']),
                    'Pre-mutation stage evidence invalid')
            require(marker.is_file() and not marker.is_symlink(), 'Previous provenance missing')
            fields = dict(line.split('=', 1) for line in marker.read_text().splitlines() if '=' in line)
            require(fields.get('commit') == proof['previous_commit'], 'Production changed since pre-mutation failure')
        elif marker.exists():
            fields = dict(line.split('=', 1) for line in marker.read_text().splitlines() if '=' in line)
            require(fields.get('source') == branch, 'Another task has production provenance; do not recover it')
        validate_manifest(app, files, allow_absent=pre_mutation)
        ledger = database_snapshot(app)
        if pre_mutation:
            require(not any(path in files and files[path] is None for path, _ in ledger),
                    'Failed candidate migration was already applied')
        for unit in ('everydayai-backend','everydayai-sync','everydayai-wecom','everydayai-conversation-actor','nginx'):
            require(subprocess.run(['systemctl','is-active','--quiet',unit]).returncode == 0, 'Service not active: ' + unit)
        require(subprocess.run(['curl','--fail','--silent','--max-time','10','-o','/dev/null',
                                'http://127.0.0.1:8000/api/health']).returncode == 0, 'Application is not ready')
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        evidence = app / '.incident-evidence' / ('release-recovery-' + stamp + '-' + owner)
        evidence.mkdir(parents=True, mode=0o700)
        with tarfile.open(evidence / 'files.tar.gz', 'w:gz') as archive:
            for name in files:
                if (app / name).is_file():
                    archive.add(app / name, arcname=name, recursive=False)
            if marker.exists():
                archive.add(marker, arcname='.release-provenance', recursive=False)
            if (app / 'frontend').is_dir():
                archive.add(app / 'frontend', arcname='frontend')
        receipt = dict(owner=owner, failed_commit=failed_commit, source=branch,
                       recovered_at=stamp, manifest=manifest, migrations=ledger,
                       evidence=str(evidence), status='recovered_not_deployed')
        (evidence / 'recovery.json').write_text(json.dumps(receipt, indent=2))
        for item in evidence.iterdir():
            item.chmod(0o600)
        check_owner(lock, owner)
        require(not active_writers(), 'Writer appeared during recovery')
        # Receipt is written while the original lock still exists. If rename
        # fails, no next publisher can acquire the old lock.
        temporary = app / ('.release-recovery.tmp.' + owner)
        temporary.write_text(json.dumps(receipt))
        temporary.chmod(0o600)
        os.replace(temporary, app / '.release-recovery')
        os.rename(lock, evidence / 'release-lock')
        print('RELEASE_RECOVERY_RESULT status=success failed_commit=' + failed_commit + ' evidence=' + str(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('owner'); parser.add_argument('failed_commit'); parser.add_argument('branch'); parser.add_argument('manifest')
    args = parser.parse_args()
    try:
        recover(args.owner, args.failed_commit, args.branch, json.loads(base64.b64decode(args.manifest)))
    except Exception as error:
        raise SystemExit('RELEASE_RECOVERY_FAILED ' + str(error))
