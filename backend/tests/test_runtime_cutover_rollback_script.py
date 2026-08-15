from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/runtime-cutover-rollback.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runtime_cutover_rollback", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Rows:
    def __init__(self, rows: list[tuple[str, str, str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str, str, str]]:
        return self._rows


class _LedgerConnection:
    def __init__(self, rows: list[tuple[str, str, str]]) -> None:
        self._rows = rows

    def execute(self, sql: str) -> _Rows:
        assert "schema_migration_ledger" in sql
        return _Rows(self._rows)


def _ledger_rows(module: ModuleType) -> tuple[list[tuple[str, str, str]], list[object]]:
    target = module._target_migrations()
    lane = module._rollback_lane()
    rows = [(identity, checksum, "applied") for identity, checksum in target.items()]
    rows.extend((item.identity, "f" * 64, "applied") for item in lane)
    return rows, lane


def test_inventory_matches_production_rollback_boundary() -> None:
    module = _module()
    target = module._target_migrations()
    lane = module._rollback_lane()

    assert len(target) == 280
    assert "221_worker_media_rpc_bigint_compatibility.sql" in target
    assert module.BOUNDARY in target
    assert module.FIRST not in target
    assert "229_tool_audit_partition_lifecycle.sql" not in target
    assert len(lane) == 89
    assert lane[0].identity == module.FIRST
    assert lane[-1].identity == module.LAST


def test_reverse_lane_accepts_contiguous_resume_only() -> None:
    module = _module()
    rows, lane = _ledger_rows(module)
    assert len(module._remaining_lane(_LedgerConnection(rows))) == 89

    resumed = rows[:-7]
    assert len(module._remaining_lane(_LedgerConnection(resumed))) == 82

    gap = rows[:-7]
    gap.pop(-10)
    with pytest.raises(module.RollbackError, match="contiguous"):
        module._remaining_lane(_LedgerConnection(gap))


def test_execute_requires_explicit_destructive_gate() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'ALLOW_RUNTIME_CUTOVER_ROLLBACK") != "true"' in source
    assert "pg_advisory_lock" in source
    assert "trg_agent_safe_activation_immutable" in source
    assert "billing user has newer credit history" in source
    assert "Runtime workers are not fully drained" in source
