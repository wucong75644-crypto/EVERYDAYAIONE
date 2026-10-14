from datetime import datetime, timezone, timedelta
from uuid import uuid4
import io
import pytest
from services.social_ops.analytics import exact_count, statistics, METRICS
from services.social_ops.metric_imports import parse_table
from services.social_ops.reviews import validate_review
from services.agent.agent_reach.contracts import ReachError, ReachItem, ReachResult


def row(metrics=None, **kwargs):
    now = datetime.now(timezone.utc)
    return {
        "id": str(uuid4()),
        "intent_id": str(uuid4()),
        "connection_id": "a",
        "platform": "xiaohongshu",
        "content_type": "image",
        "observed_at": now,
        "published_at": now - timedelta(hours=5),
        "metrics": metrics or {},
        "coverage": {"scope": "public_cumulative", "denominator": None},
        **kwargs,
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        (0, 0),
        ("0", 0),
        ("12", 12),
        ("1万", None),
        ("1,000", None),
        (-1, None),
        (True, None),
        (1.2, None),
        ("100+", None),
        ("99999999999999999", None),
    ],
)
def test_exact_metric(raw, expected):
    assert exact_count(raw) == expected


def test_stats_null_zero_denominator_and_coverage():
    a = row({"likes": 0, "comments": None, "views": 0})
    b = row(
        {"likes": 1, "comments": 2, "saves": 0, "shares": 0, "views": 0},
        coverage={"scope": "creator_cumulative", "denominator": "views"},
    )
    c = row(
        {"likes": 0, "comments": 0, "saves": 0, "shares": 0, "views": 20},
        coverage={"scope": "creator_cumulative", "denominator": "views"},
    )
    result = statistics([a, b, c])
    assert result["coverage"]["likes"] == {"available": 3, "total": 3}
    assert result["coverage"]["comments"]["available"] == 2
    assert [r["engagement_rate"] for r in result["contents"]] == [None, None, 0]
    assert len(result["groups"]) == 2
    assert all(g["insufficient"] for g in result["groups"])


def test_stats_age_platform_and_account_groups_latest():
    a = row({"likes": 1})
    b = {
        **a,
        "id": str(uuid4()),
        "metrics": {"likes": 2},
        "observed_at": a["observed_at"] + timedelta(hours=1),
    }
    others = [
        row({"likes": 0}, platform="reddit"),
        row({"likes": 0}, connection_id="other"),
        row({"likes": 0}, published_at=None),
        row({"likes": 0}, content_type="video"),
    ]
    result = statistics([a, b, *others])
    assert result["sample_size"] == 5 and result["snapshot_count"] == 6
    assert result["contents"][0]["metrics"]["likes"] == 2
    assert len(result["groups"]) == 5


def test_csv_and_xlsx_bounded_no_formula():
    from openpyxl import Workbook

    headers, rows = parse_table(b"likes,views\n0,\n", "x.csv")
    assert rows == [{"likes": "0", "views": ""}]
    wb = Workbook()
    wb.active.append(["likes", "views"])
    wb.active.append([0, None])
    raw = io.BytesIO()
    wb.save(raw)
    assert parse_table(raw.getvalue(), "x.xlsx")[1][0]["likes"] == "0"
    for data in (b"a,a\n1,2", b"a\n=SUM(1)", b"a\n1,2"):
        with pytest.raises(ReachError):
            parse_table(data, "x.csv")


def test_report_no_invented_fact_numbers_or_comment_evidence():
    payload = {
        "statistics": {"facts": [{"id": "f"}]},
        "sources": [{"id": "s", "coverage": {"content_kind": "metrics"}}],
    }
    output = {
        "observed_fact_ids": ["f"],
        "possible_causes": [],
        "comment_insights": [],
        "suggestions": [],
        "insufficient_evidence": [],
    }
    assert validate_review(output, payload) == output
    with pytest.raises(ReachError):
        validate_review({**output, "observed_fact_ids": ["missing"]}, payload)
    with pytest.raises(ReachError):
        validate_review({**output, "insufficient_evidence": ["阅读达到100"]}, payload)
    with pytest.raises(ReachError):
        validate_review(
            {
                **output,
                "comment_insights": [
                    {"text": "需求", "evidence": [{"snapshot_id": "s"}]}
                ],
            },
            payload,
        )


def test_structured_metrics_survive_pagination():
    from services.agent.agent_reach.contracts import ReachRequest

    result = ReachResult(
        "xiaohongshu",
        "xhs-rest",
        [
            ReachItem(
                "t",
                "https://www.xiaohongshu.com/explore/abc",
                "body",
                metrics={"likes": 0},
                metric_metadata={"author_id": "a"},
            )
        ],
    )
    request = ReachRequest(
        task="read",
        action="read",
        platform="xiaohongshu",
        url="https://www.xiaohongshu.com/explore/abc",
    )
    source = result.to_agent_result(request).metadata["sources"][0]
    assert (
        source["metrics"]["likes"] == 0
        and source["metric_metadata"]["author_id"] == "a"
    )


@pytest.mark.parametrize(
    "mode", ["valid", "fake_fact", "fake_quote", "missing_skill", "insufficient_input"]
)
async def test_review_model_fixed_skill_and_evidence_runtime(monkeypatch, mode):
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from core.config import Settings
    from core.db_scope import DatabaseScope, DatabaseAccessKind
    from tests.test_skill_runtime import Source, item
    from tests.test_scheduled_skill_snapshots import snapshot
    from services.social_ops.production import ProductionWorker
    from services.adapters.types import StreamChunk

    source = Source(
        [
            item(
                key="easel-content-postmortem", tools=(), execution_modes=("scheduled",)
            )
        ],
        body="固定复盘方法",
    )
    pins = snapshot(source)
    for pin in pins["skills"]:
        pin["allowed_tool_names"] = []
    settings = Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        jwt_secret_key="test-only",
        skill_catalog_enabled=True,
        skill_runtime_enabled=True,
        agent_loop_model="deepseek-v4-pro",
    )
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "services.skills.scheduled.source_for_executor", lambda *args: source
    )
    sid = str(uuid4())
    requests = []
    ledger = []
    output = {
        "observed_fact_ids": ["missing" if mode == "fake_fact" else "fact"],
        "possible_causes": [],
        "comment_insights": [],
        "insufficient_evidence": ["有限样本不能说明整体表现"],
        "suggestions": [
            {
                "kind": "topic",
                "text": "尝试解释性选题并验证反馈",
                "evidence": [
                    {"reference_id": "E999" if mode == "fake_quote" else "E1"}
                ],
            }
        ],
    }

    class Session:
        async def stream_chat(self, messages, **kwargs):
            requests.append((messages, kwargs))
            yield StreamChunk(
                content=json.dumps(output),
                finish_reason="stop",
                prompt_tokens=30,
                completion_tokens=20,
            )

        close = AsyncMock()

    session = Session()
    monkeypatch.setattr(
        "services.model_gateway.get_model_gateway",
        lambda: SimpleNamespace(open_chat=lambda req: session),
    )

    @asynccontextmanager
    async def lock(*args, **kwargs):
        yield SimpleNamespace(set_actual=lambda value: None)

    monkeypatch.setattr("services.credit_service.CreditService.credit_lock", lock)
    scope = DatabaseScope(
        actor_user_id=str(uuid4()),
        org_id=str(uuid4()),
        access_kind=DatabaseAccessKind.RUNTIME,
    )
    w = ProductionWorker(SimpleNamespace(pool=None), scope)
    w.record_usage = lambda run, entry: ledger.append(dict(entry))
    run = {
        "id": uuid4(),
        "stage_index": 0,
        "stages": [pins],
        "kind": "postmortem",
        "allowance": 5,
        "lease_token": uuid4(),
        "outputs": [],
        "input": {
            "snapshot_hash": "fixed-input",
            "sources": [
                {
                    "id": sid,
                    "body": "真实指标证据",
                    "coverage": {"content_kind": "metrics"},
                }
            ],
            "statistics": {"facts": [{"id": "fact"}]},
        },
    }
    if mode == "insufficient_input":
        run["input"]["sources"] = []
    if mode == "missing_skill":
        source.load.side_effect = RuntimeError("pinned dependency unavailable")
    if mode != "valid":
        with pytest.raises(Exception):
            await w.execute_stage(run)
        if mode in {"missing_skill", "insufficient_input"}:
            assert not requests
        return
    result, manifest = await w.execute_stage(run)
    assert result["suggestions"][0]["evidence"] == [
        {"snapshot_id": sid, "quote": "真实指标证据"}
    ]
    assert (
        manifest["skill_snapshot"] == pins and manifest["input_hash"] == "fixed-input"
    )
    assert (
        manifest["tokens"] == 50
        and manifest["actual_credits"] > 0
        and manifest["monetary_cost"] is None
    )
    assert ledger[-1]["status"] == "response_received" and requests[0][1][
        "response_format"
    ] == {"type": "json_object"}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", 0),
        ("0.56", 0.56),
        ("1", 1),
        ("56%", None),
        ("56", None),
        ("NaN", None),
        (-0.1, None),
    ],
)
def test_controlled_completion_ratio_not_synthesized(value, expected):
    from services.social_ops.analytics import exact_metric

    assert exact_metric("completion_rate", value) == expected
