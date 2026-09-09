"""只读诊断：临时 fixture，删除/记录均 mock，不触发真实业务或外部服务。

运行：PYTHONPATH=backend APP_ENV=testing DATABASE_URL=postgresql://test:test@127.0.0.1:1/test
      JWT_SECRET_KEY=test-only REDIS_PORT=1 python <本文件>
"""

import asyncio
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from loguru import logger

from services.agent.file_delete_mixin import FileDeleteMixin
from services.agent.file_id import compute_fid
from services.agent.file_path_cache import FilePathCache
from services.agent.sandbox_tool_mixin import SandboxToolMixin
from services.file_executor import FileExecutor


async def main(expect_fixed=False):
    logger.remove()
    base = Path(tempfile.mkdtemp(prefix="other-file-tools-audit-")).resolve()
    workspace = base / "workspace"
    executor = FileExecutor(str(workspace))
    outside = base / "outside-fixture.csv"
    outside.write_text("fixture\n1\n")
    inside = workspace / "目录" / "inside.csv"
    inside.parent.mkdir()
    inside.write_text("fixture\n1\n")
    try:
        executor.resolve_safe_path(str(outside))
    except PermissionError:
        print("real_workspace_guard_rejects_outside: true")
    else:
        raise AssertionError("fixture should be outside workspace")

    cache = FilePathCache()
    cache.register(inside.name, workspace=str(inside))
    cache.register("目录/inside.csv", workspace=str(inside))
    owner = SimpleNamespace(
        conversation_id="isolated-audit", org_id="org",
        _record_deleted_files=MagicMock(),
    )
    scenarios = [
        ("outside_absolute", {"files": [str(outside)]}),
        ("valid_fid", {"file_ids": [compute_fid("org", "目录/inside.csv")]}),
        ("unknown_fid", {"file_ids": ["fid_00000000"]}),
        ("wrong_org_fid", {"file_ids": [compute_fid("other-org", "目录/inside.csv")]}),
    ]
    with patch("services.agent.file_path_cache.get_file_cache", return_value=cache):
        for name, args in scenarios:
            with patch.object(executor, "resolve_safe_path", wraps=executor.resolve_safe_path) as guard, patch("os.remove") as remove:
                result = await FileDeleteMixin._file_delete(owner, executor, args, None)
                print(json.dumps({"scenario": name, "status": result.status, "remove_mock_calls": remove.call_count, "workspace_guard_calls": guard.call_count}))
                if expect_fixed:
                    expected = 1 if name == "valid_fid" else 0
                    assert remove.call_count == expected
                    if name in {"valid_fid", "outside_absolute"}:
                        assert guard.call_count > 0
                    if name != "valid_fid":
                        assert result.is_failure

        sandbox_owner = SimpleNamespace(
            conversation_id="isolated-audit", _get_workspace_dir=lambda: str(workspace),
        )
        SandboxToolMixin._register_files_from_output(sandbox_owner, "'../outside-fixture.csv'")
        registered_outside = cache.resolve(outside.name, "analyze") == str(outside)
        print("sandbox_stdout_registers_outside_workspace:", registered_outside)
        if expect_fixed:
            assert not registered_outside

    assert outside.exists() and inside.exists()
    print("real_fixture_files_still_exist: true")
    for name in ["backend/services/agent/file_delete_mixin.py", "backend/services/agent/sandbox_tool_mixin.py"]:
        baseline = subprocess.check_output(["git", "show", f"e243ba2c0d545d8afca3ae930a40389276b7c123:{name}"])
        print("EQUAL_TO_DEPLOYED", baseline == Path(name).read_bytes(), name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-fixed", action="store_true")
    asyncio.run(main(parser.parse_args().expect_fixed))
