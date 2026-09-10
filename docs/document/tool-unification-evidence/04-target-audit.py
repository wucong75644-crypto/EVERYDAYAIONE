"""Read-only production-code audit. All mutations are in TemporaryDirectory."""
import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.config import get_settings
from services.agent.tool_executor import ToolExecutor
from services.agent.file_path_cache import get_file_cache
from services.agent.file_id import compute_fid
from services.handlers.resource_manifest import ResourceAsset, ResourceManifest
from services.file_executor import FileExecutor
from services.tools.runtime_context import resolve_resources
from tests.tool_runtime_support import IdentityDB, MockHandlerExecutor

async def main():
    out = []
    with tempfile.TemporaryDirectory(prefix='tool04-audit-') as temp:
        settings = get_settings()
        settings.file_workspace_root = temp
        settings.file_workspace_enabled = True
        files = FileExecutor(temp, 'u1', 'o1')
        root = Path(files.workspace_root)
        real = root / 'uploads' / 'report.csv'
        real.parent.mkdir(parents=True)
        real.write_text('x\n1\n')
        owner = ToolExecutor(IdentityDB(), 'u1', 'audit-main', 'o1')
        cache = get_file_cache(owner.conversation_id)
        cache.register('uploads/report.csv', workspace=str(real))

        result = resolve_resources(owner, 'file_analyze', {'path':'different/report.csv', 'scope':'workspace'})
        assert Path(result['path']) == real
        out.append({'case':'explicit_path_rewritten', 'observed':'different/report.csv resolved to uploads/report.csv'})

        fid = compute_fid('o1', 'uploads/report.csv')
        assert resolve_resources(owner, 'file_delete', {'file_ids':[fid]})['files'] == [str(real)]
        cold = ToolExecutor(IdentityDB(), 'u1', 'audit-cold-worker', 'o1')
        try:
            resolve_resources(cold, 'file_delete', {'file_ids':[fid]})
        except PermissionError as exc:
            assert str(exc) == 'resource_id_unavailable'
        else:
            raise AssertionError('Expected lost-ID failure')
        out.append({'case':'cold_worker_fid', 'observed':'same existing file ID unavailable without conversation cache'})

        ghost = ToolExecutor(IdentityDB(), 'u1', 'audit-ghost', 'o1', resource_manifest=ResourceManifest('t','m',(
            ResourceAsset('a','ghost.csv','uploads/ghost.csv','text/csv',4,''),), 'test'))
        result = await ghost._search_manifest(files, {})
        assert '[fid_' in result.summary and not (root / 'uploads/ghost.csv').exists()
        out.append({'case':'nonexistent_manifest_hit', 'observed':'missing file listed with actionable-looking fid'})

        special = root / 'uploads' / 'report | final.csv'
        special.write_text('x\n2\n')
        result = await owner._search_files(files, {'keyword':'report | final'})
        broken = compute_fid('o1','uploads/report')
        assert broken in result.summary
        try:
            resolve_resources(owner, 'file_delete', {'file_ids':[broken]})
        except PermissionError:
            pass
        else:
            raise AssertionError('Expected bad ID')
        out.append({'case':'search_display_parsing', 'observed':'filename containing pipe rendered with ID for truncated nonexistent path'})

        checked = []
        async def approve(call, context, decision):
            real.write_text('REPLACEMENT')
            return True
        e = MockHandlerExecutor(conversation_id='audit-version', agent_domain='general', tool_confirmer=approve)
        async def mock_handler(name, args):
            checked.append(Path(args['files'][0]).read_text())
            return 'mock business only'
        e.handler.side_effect = mock_handler
        await e.execute('file_delete', {'files':['uploads/report.csv']})
        assert checked == ['REPLACEMENT']
        out.append({'case':'confirmation_file_version', 'observed':'after approval same path with changed bytes reached mock Handler once'})

        from services.scheduler.chat_task_manager import ChatTaskManager
        rows = [{'id':'12345678-aaaa','name':'A'}, {'id':'12345678-bbbb','name':'B'}]
        class Query:
            def table(self, *a): return self
            def select(self, *a): return self
            def eq(self, *a): return self
            def execute(self): return SimpleNamespace(data=rows)
        found = await ChatTaskManager(Query(), 'u1', 'o1')._find_task(task_id='12345678')
        assert found == rows[0]
        out.append({'case':'ambiguous_task_id', 'observed':'two matching short IDs silently select first row'})

        record = {'id':'record1','relative_path':str(real.relative_to(Path(temp).resolve())), 'oss_object_key':'mock-backup'}
        owner._find_deleted_record = AsyncMock(return_value=record)
        owner._mark_restored = AsyncMock()
        real.write_text('CURRENT FILE')
        def mock_download(key, destination): Path(destination).write_text('OLD BACKUP')
        fake_oss = SimpleNamespace(bucket=SimpleNamespace(get_object_to_file=mock_download))
        with patch('services.oss_service.get_oss_service', return_value=fake_oss):
            result = await owner._restore_file(files, {'filename':'report.csv'}, settings)
        assert result.status == 'success' and real.read_text() == 'OLD BACKUP'
        out.append({'case':'restore_destination_conflict', 'observed':'real restore Handler with mocked OSS overwrites existing temp file'})

        from services.agent.data_query_cache import _compute_file_fingerprint, ensure_parquet_cache_csv
        import pandas as pd
        large = root / 'large.csv'
        prefix = 'value\n' + ('1\n' * 524288)
        large.write_text(prefix + '2\n')
        staging = root / 'staging'
        staging.mkdir()
        first_fp = _compute_file_fingerprint(str(large))
        first_path, _ = await ensure_parquet_cache_csv(str(large),str(staging))
        large.write_text(prefix + '9\n')
        second_path, _ = await ensure_parquet_cache_csv(str(large),str(staging))
        assert _compute_file_fingerprint(str(large)) == first_fp
        assert first_path == second_path
        assert pd.read_parquet(second_path).iloc[-1,0] == 2
        out.append({'case':'analysis_stale_tail', 'observed':'CSV tail changed beyond 1 MiB; real converter reused Parquet containing old value 2 instead of 9'})
    print(json.dumps(out, ensure_ascii=False, indent=2))

asyncio.run(main())
