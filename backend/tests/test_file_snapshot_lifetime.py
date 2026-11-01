"""Real readers close before NAS snapshots are unlinked, including retained tracebacks."""
import asyncio
import errno
import os
import threading
from pathlib import Path

import openpyxl
import pytest

from services.agent.file_scanners import make_scanner
from services.file_resources import source_snapshot


def workbook(path, multiple=True):
    wb = openpyxl.Workbook()
    wb.active.append(['名称', '数量'])
    wb.active.append(['商品', 3])
    if multiple:
        wb.create_sheet('备注').append(['说明'])
    wb.save(path)
    wb.close()


def descriptors(path):
    identity = path.stat()
    found = []
    for fd in range(256):
        try:
            info = os.fstat(fd)
        except OSError:
            continue
        if (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
            found.append(fd)
    return found


@pytest.mark.parametrize('multiple', [False, True])
def test_real_scanner_releases_reader_while_failure_traceback_is_retained(tmp_path, multiple):
    source = tmp_path / 'report.xlsx'
    workbook(source, multiple)
    scanner = make_scanner(str(source))
    assert descriptors(source), 'test must actually open the source'
    scanner.scan()
    try:
        raise ValueError('AI decision rejected')
    except ValueError as error:
        retained = error
        scanner.release_cached()
    assert retained.__traceback__ is not None
    assert not descriptors(source)
    assert scanner._cached_df is None and not scanner._cached_sheet_dfs


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['failure', 'cancel', 'success'])
async def test_cleanup_failure_does_not_mask_primary_failure_or_cancel(tmp_path, monkeypatch, kind):
    import tempfile
    source = tmp_path / 'report.csv'
    source.write_text('data')
    primary = asyncio.CancelledError() if kind == 'cancel' else ValueError('original parse failure')
    def fail_cleanup(self):
        raise OSError(errno.ENOTEMPTY, 'NAS ghost')
    monkeypatch.setattr(tempfile.TemporaryDirectory, 'cleanup', fail_cleanup)
    with pytest.raises(BaseException) as seen:
        async with source_snapshot(str(source), str(tmp_path / 'staging')):
            if kind != 'success':
                raise primary
    if kind != 'success':
        assert seen.value is primary
    else:
        assert isinstance(seen.value, OSError) and seen.value.errno == errno.ENOTEMPTY


@pytest.mark.asyncio
async def test_cancelled_scanner_creation_is_closed_before_snapshot_cleanup(tmp_path, monkeypatch):
    from services.agent.data_query_cache import ensure_parquet_cache
    import services.agent.file_scanners as scanners
    source = tmp_path / 'report.xlsx'
    workbook(source)
    entered, release = threading.Event(), threading.Event()
    import tempfile
    original_cleanup = tempfile.TemporaryDirectory.cleanup
    def checked_cleanup(directory):
        for candidate in Path(directory.name).glob('*.xlsx'):
            assert not descriptors(candidate), 'cleanup must not unlink a reader still open on NAS'
        original_cleanup(directory)
    monkeypatch.setattr(tempfile.TemporaryDirectory, 'cleanup', checked_cleanup)
    original = scanners.make_scanner
    def blocked(*args):
        scanner = original(*args)
        entered.set()
        assert release.wait(5)
        return scanner
    monkeypatch.setattr(scanners, 'make_scanner', blocked)
    staging = tmp_path / 'staging'
    snapshot_path = None
    async def run():
        nonlocal snapshot_path
        async with source_snapshot(str(source), str(staging)) as snapshot:
            snapshot_path = Path(snapshot)
            await ensure_parquet_cache(snapshot, None, str(staging))
    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and snapshot_path.exists()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not snapshot_path.exists()
    assert not list(staging.glob('.source-*'))


@pytest.mark.asyncio
@pytest.mark.parametrize('multiple', [False, True])
async def test_real_conversion_reuses_cache_and_closes_all_source_handles(tmp_path, monkeypatch, multiple):
    from unittest.mock import AsyncMock
    import services.agent.file_ai_judge as judge
    from services.agent.file_ai_decision import AIDecision, SheetDecision
    from services.agent.data_query_cache import ensure_parquet_cache
    source = tmp_path / 'report.xlsx'
    workbook(source, multiple)
    decision = AIDecision(header_row=1, data_start_row=2,
        sheets=[SheetDecision('Sheet', 'data'), SheetDecision('备注', 'skip')] if multiple else [])
    provider = AsyncMock(return_value=decision)
    monkeypatch.setattr(judge, 'adjudicate', provider)
    staging = tmp_path / 'staging'
    async with source_snapshot(str(source), str(staging)) as path:
        snapshot = Path(path)
        first = await ensure_parquet_cache(path, None, str(staging))
        assert Path(first[0]).is_file()
        assert not descriptors(snapshot)
        second = await ensure_parquet_cache(path, None, str(staging))
        assert first[0] == second[0]
        assert not descriptors(snapshot)
    provider.assert_awaited_once()
    assert not list(staging.glob('.source-*'))
    assert source.exists()


@pytest.mark.asyncio
async def test_actual_governance_failure_survives_cleanup_with_traceback(tmp_path, monkeypatch):
    import services.agent.file_ai_judge as judge
    from services.agent.data_query_cache import ensure_parquet_cache
    source = tmp_path / 'report.xlsx'
    workbook(source)
    primary = judge.FileAnalyzeError(error_category='llm_output_invalid', error_summary='invalid role',
        retryable=False, suggested_action='retry_immediately', user_message='解析裁决失败')
    async def fail(_):
        raise primary
    monkeypatch.setattr(judge, 'adjudicate', fail)
    staging = tmp_path / 'staging'
    with pytest.raises(judge.FileAnalyzeError) as seen:
        async with source_snapshot(str(source), str(staging)) as path:
            snapshot = Path(path)
            await ensure_parquet_cache(path, None, str(staging))
    assert seen.value is primary and seen.value.__traceback__ is not None
    assert not descriptors(source)
    assert not snapshot.exists() and not list(staging.glob('.source-*'))
