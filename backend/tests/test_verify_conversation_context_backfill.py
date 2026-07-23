"""统一 ConversationItem 历史回填只读门禁测试。"""

import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import verify_conversation_context_backfill as verifier


BACKEND = Path(__file__).resolve().parents[1]


def _connection(row: dict[str, int]) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    return connection


def _valid_row() -> dict[str, int]:
    return {
        "eligible_messages": 12,
        "projected_messages": 12,
        "missing_messages": 0,
        "orphan_tool_results": 0,
        "duplicate_sequences": 0,
        "invalid_item_revisions": 0,
        "missing_artifacts": 0,
    }


def test_verify_backfill_reports_passing_invariants() -> None:
    connection = _connection(_valid_row())

    result = verifier.verify_backfill(connection)

    assert result.passed is True
    assert result.to_dict()["eligible_messages"] == 12
    query, params = connection.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert "source_message_id" in query.as_string()
    assert "item_type = 'tool_result'" in query.as_string()
    assert "context_revision * 1000" in query.as_string()
    assert "conversation_artifacts" in query.as_string()
    assert params == {}


def test_verify_backfill_scopes_every_fact_check_to_conversation() -> None:
    connection = _connection(_valid_row())
    conversation_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    verifier.verify_backfill(connection, conversation_id)

    query, params = connection.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert query.as_string().count("conversation_id = %(conversation_id)s") == 5
    assert params == {"conversation_id": conversation_id}


def test_verify_backfill_fails_closed_without_result() -> None:
    connection = _connection(_valid_row())
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None

    with pytest.raises(RuntimeError, match="returned no result"):
        verifier.verify_backfill(connection)


@pytest.mark.parametrize(
    "metric",
    [
        "missing_messages",
        "orphan_tool_results",
        "duplicate_sequences",
        "invalid_item_revisions",
        "missing_artifacts",
    ],
)
def test_any_invariant_violation_blocks_cutover(metric: str) -> None:
    row = _valid_row()
    row[metric] = 1

    result = verifier.verify_backfill(_connection(row))

    assert result.passed is False


def test_main_is_read_only_and_returns_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    with (
        patch("sys.argv", ["verify-backfill"]),
        patch.object(verifier, "load_env"),
        patch.object(verifier.psycopg, "connect", return_value=connection_context),
        patch.object(
            verifier,
            "verify_backfill",
            return_value=verifier.VerificationResult(**_valid_row()),
        ),
    ):
        outcome = verifier.main()

    assert outcome == 0
    connection.execute.assert_called_once_with("SET TRANSACTION READ ONLY")
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    assert '"passed": true' in capsys.readouterr().out


def test_main_returns_nonzero_when_gate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    failed = _valid_row()
    failed["missing_messages"] = 2
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    with (
        patch("sys.argv", ["verify-backfill"]),
        patch.object(verifier, "load_env"),
        patch.object(verifier.psycopg, "connect", return_value=connection_context),
        patch.object(
            verifier,
            "verify_backfill",
            return_value=verifier.VerificationResult(**failed),
        ),
    ):
        outcome = verifier.main()

    assert outcome == 2


def test_script_can_start_from_direct_path() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(BACKEND / "scripts/verify_conversation_context_backfill.py"),
            "--help",
        ],
        cwd=BACKEND,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert "--conversation-id" in completed.stdout
