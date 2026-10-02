"""Environment sync preserves production state and rejects a stale database."""
import getpass
import importlib.util
from pathlib import Path
import subprocess

from dotenv import dotenv_values
import psycopg
import pytest

from tests.test_scheduled_task_draft_delete_integration import postgres_socket  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ENV = module('env-patch')
DB = module('verify-production-database')


def test_only_search_keys_change_and_all_production_features_survive(tmp_path):
    target = tmp_path / '.env'
    target.write_text("# real production configuration\nDATABASE_URL=postgresql://real/live\n"
                      "SKILL_CATALOG_ENABLED=true\nSKILL_STORAGE_ROOT=/nas/skills\n"
                      "CALLBACK_TOKEN=fixture-production-token\nCONVERSATION_ACTOR_WEB_ENABLED=true\n"
                      "OSS_CDN_DOMAIN=correct-cdn.example\n")
    source = tmp_path / 'stale-source'
    source.write_text('DATABASE_URL=postgresql://old/backup\nWEB_SEARCH_PROVIDER=doubao\n'
                      'WEB_SEARCH_ARK_API_KEY=fixture-api-key\n')
    original = target.read_bytes()
    before = dotenv_values(target)
    ENV.apply_patch_file(target, ENV.build_patch(source, ['WEB_SEARCH_PROVIDER', 'WEB_SEARCH_ARK_API_KEY']))
    after = dotenv_values(target)
    assert all(after[k] == v for k, v in before.items())
    assert after['WEB_SEARCH_ARK_API_KEY'] == 'fixture-api-key'
    assert after['WEB_SEARCH_PROVIDER'] == 'doubao'
    backups = list(tmp_path.glob('.env.backup.patch.*'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('patch', [{}, {'DATABASE_URL': 'postgresql://wrong/old'},
                                 {'BAD KEY': 'x'}, {'KEY': None}, {'KEY': 'a\nb'}, {'KEY': "a'b"}])
def test_invalid_or_database_patch_leaves_original_byte_identical(tmp_path, patch):
    target = tmp_path / '.env'
    target.write_bytes(b'DATABASE_URL=live\nKEEP=value\n')
    with pytest.raises(ValueError):
        ENV.apply_patch_file(target, patch)
    assert target.read_bytes() == b'DATABASE_URL=live\nKEEP=value\n'
    assert not list(tmp_path.glob('.env.backup.patch.*'))


def test_missing_selected_key_and_whole_file_upload_fail_before_network(tmp_path):
    source = tmp_path / '.env'
    source.write_text('KEY=value\n')
    for keys in ([], ['MISSING']):
        with pytest.raises(ValueError):
            ENV.build_patch(source, keys)
    result = subprocess.run(['bash', str(ROOT / 'deploy/upload-env.sh')], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode != 0 and '--key' in result.stderr


def test_special_literal_value_never_executes_shell(tmp_path):
    target = tmp_path / '.env'
    target.write_text('KEEP=value\n')
    sentinel = tmp_path / 'must-not-exist'
    value = f'$(touch {sentinel}) `touch {sentinel}` # literal $VALUE'
    ENV.apply_patch_file(target, {'KEY': value})
    assert dotenv_values(target, interpolate=False)['KEY'] == value
    consumed = subprocess.check_output(['bash', '-c', 'source "$1"; printf "%s" "$KEY"',
                                        'bash', str(target)], text=True)
    assert consumed == value
    assert not sentinel.exists()


def test_patch_failure_preserves_original_and_does_not_print_secret(tmp_path, monkeypatch):
    target = tmp_path / '.env'
    target.write_bytes(b'KEEP=original\n')
    def fail(*args, **kwargs):
        raise OSError('fixture failure')
    monkeypatch.setattr(ENV.os, 'replace', fail)
    with pytest.raises(OSError):
        ENV.apply_patch_file(target, {'API_KEY': 'fixture-sensitive-value'})
    assert target.read_bytes() == b'KEEP=original\n'
    assert not list(tmp_path.glob('.env.patch-*'))


def test_backup_is_private_at_creation_even_with_public_umask(tmp_path, monkeypatch):
    import os
    target = tmp_path / '.env'
    target.write_text('KEEP=fixture-secret\n')
    real_open = ENV.os.open
    observed = []
    def checking_open(path, flags, mode=0o777, *args, **kwargs):
        fd = real_open(path, flags, mode, *args, **kwargs)
        if '.env.backup.patch.' in str(path):
            observed.append(os.fstat(fd).st_mode & 0o777)
        return fd
    monkeypatch.setattr(ENV.os, 'open', checking_open)
    previous = os.umask(0o022)
    try:
        ENV.apply_patch_file(target, {'SEARCH_KEY': 'fixture-new-value'})
    finally:
        os.umask(previous)
    assert observed == [0o600]


def test_verified_database_matches_and_stale_name_or_oid_is_rejected(postgres_socket):
    with psycopg.connect(host=postgres_socket, dbname='postgres', user=getpass.getuser()) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        name, oid = conn.execute('SELECT current_database(),oid FROM pg_database WHERE datname=current_database()').fetchone()
        DB.verify_identity(conn, {'database': name, 'database_oid': oid})
        for expected in ({'database': 'old-backup', 'database_oid': oid},
                         {'database': name, 'database_oid': oid + 1}, {}):
            with pytest.raises(ValueError, match='IDENTITY_MISMATCH'):
                DB.verify_identity(conn, expected)


def test_database_guard_runs_before_any_frontend_or_backend_sync():
    script = (ROOT / 'deploy/deploy.sh').read_text()
    main = script[script.index('# 部署流程'):]
    assert '< deploy/verify-production-database.py' in script
    assert script.index('< deploy/verify-production-database.py') < script.index('# 部署流程')
    assert 'sync_frontend' in main and 'sync_backend' in main


def test_release_guard_fails_before_candidate_invalidation_and_executor():
    release = (ROOT / 'deploy/release.sh').read_text()
    segment = release[release.index('database_guard=deploy/verify-production-database.py'):]
    assert segment.index('< "$database_guard" || fail') < segment.index('release_remote_state invalidate')
    assert segment.index('< "$database_guard" || fail') < segment.index('release_executor_state=in_flight')
    assert '[[ -f "$task_file" ]] || continue' in release


@pytest.mark.parametrize('guard_status', [0, 1])
def test_actual_shell_preflight_preserves_candidate_when_database_mismatches(tmp_path, guard_status):
    release = (ROOT / 'deploy/release.sh').read_text()
    start = release.index('database_guard=deploy/verify-production-database.py')
    end = release.index('release_executor_state=in_flight', start)
    marker = tmp_path / 'candidate'
    marker.write_text('previously-tested-commit')
    setup = '''set -eu
SERVER_PORT=22; SERVER_USER=fixture; SERVER_HOST=fixture
repo_root=$1; marker=$2; guard_status=$3
fail() { exit 1; }
ssh() { return "$guard_status"; }
release_remote_state() { rm -- "$marker"; }
'''
    result = subprocess.run(['bash', '-c', setup + release[start:end], 'bash', str(ROOT),
                             str(marker), str(guard_status)], cwd=ROOT, capture_output=True)
    assert result.returncode == guard_status
    assert marker.exists() == bool(guard_status)
    if marker.exists():
        assert marker.read_text() == 'previously-tested-commit'
