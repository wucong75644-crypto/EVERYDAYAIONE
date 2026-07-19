"""Evidence 分级 model_view 测试。"""

from services.agent.runtime.context.providers.evidence import (
    build_evidence_model_view,
)
from services.agent.runtime.artifact_ledger import (
    ArtifactEvidence,
    ArtifactKind,
    ArtifactSource,
    ArtifactStatus,
)
from services.handlers.data_context_snapshot import DataContextSnapshot


def _projection(rows):
    return build_evidence_model_view(
        artifact_id="artifact-1",
        source="erp_agent",
        rows=rows,
        columns=[{"name": "platform"}, {"name": "valid_orders"}],
        file_ref=None,
        query_scope={"date": "2026-07-17"},
        metric_definitions={"valid_orders": "付款且未关闭订单数"},
    )


def test_small_evidence_keeps_complete_rows() -> None:
    rows = [{"platform": "淘宝", "valid_orders": 414}]

    result = _projection(rows)

    assert result.model_view["tier"] == "full"
    assert result.model_view["rows"] == rows
    assert result.model_view["query_scope"] == {"date": "2026-07-17"}
    assert result.byte_size <= 8 * 1024
    assert len(result.content_hash) == 64


def test_medium_evidence_keeps_only_edge_sample() -> None:
    rows = [
        {"index": index, "value": "中" * 300}
        for index in range(20)
    ]

    result = _projection(rows)

    assert result.model_view["tier"] == "sampled"
    assert [row["index"] for row in result.model_view["sample_rows"]] == [
        0, 1, 2, 17, 18, 19,
    ]
    assert "rows" not in result.model_view


def test_large_evidence_keeps_metadata_without_rows() -> None:
    rows = [
        {"index": index, "value": "大" * 1000}
        for index in range(30)
    ]

    result = _projection(rows)

    assert result.model_view["tier"] == "metadata"
    assert result.model_view["row_count"] == 30
    assert "rows" not in result.model_view
    assert "sample_rows" not in result.model_view


def test_file_backed_evidence_uses_reference_tier() -> None:
    result = build_evidence_model_view(
        artifact_id="artifact-file",
        source="file_agent",
        rows=None,
        columns=[],
        file_ref={"artifact_id": "file-1"},
        query_scope={},
        metric_definitions={},
    )

    assert result.model_view["tier"] == "reference"
    assert result.model_view["file_ref"] == {"artifact_id": "file-1"}
    assert result.model_view["row_count"] is None


def test_snapshot_fallback_renders_old_rows_and_limits_automatic_evidence() -> None:
    evidence = tuple(
        ArtifactEvidence(
            kind=ArtifactKind.DATA_RESULT,
            source=ArtifactSource.TOOL_RESULT,
            status=ArtifactStatus.READY,
            fingerprint=f"artifact-{index}",
            payload={
                "data": [{"value": index}],
                "columns": [{"name": "value"}],
                "source": "erp_agent",
                "metadata": {"query_scope": {"index": index}},
            },
        )
        for index in range(6)
    )

    prompt = DataContextSnapshot(evidence).render_prompt()

    assert '"artifact_id":"artifact-0"' in prompt
    assert '"rows":[{"value":0}]' in prompt
    assert '"artifact_id":"artifact-4"' in prompt
    assert '"artifact_id":"artifact-5"' not in prompt
