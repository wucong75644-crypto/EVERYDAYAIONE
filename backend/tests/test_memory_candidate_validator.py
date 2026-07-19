"""通用记忆候选协议与确定性证据门禁测试。"""

from __future__ import annotations

import pytest

from services.memory.candidate_validator import validate_memory_candidate
from services.memory.contracts import parse_memory_candidate


def _candidate(
    *,
    claim: str = "用户偏好回答先给结论",
    kind: str = "preference",
    scope: str = "long_term",
    explicitness: str = "explicit",
    message_id: str = "u1",
    quote: str = "以后回答先给我结论",
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> dict:
    return {
        "claim": claim,
        "kind": kind,
        "scope": scope,
        "explicitness": explicitness,
        "evidence": [{"message_id": message_id, "quote": quote}],
        "valid_from": valid_from,
        "valid_until": valid_until,
        "attributes": {},
    }


def _messages(content: str = "以后回答先给我结论") -> list[dict]:
    return [
        {"id": "u1", "role": "user", "content": content},
        {"id": "a1", "role": "assistant", "content": "好的，我会先给结论"},
    ]


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_accepts_explicit_long_term_candidate_with_exact_user_quote():
    candidate = parse_memory_candidate(_candidate())

    result = validate_memory_candidate(candidate, _messages())

    assert result.accepted is True
    assert result.issues == ()


@pytest.mark.parametrize("field", ["claim", "kind", "scope", "explicitness"])
def test_parser_rejects_missing_required_text(field):
    raw = _candidate()
    raw[field] = ""

    with pytest.raises(ValueError, match=field):
        parse_memory_candidate(raw)


def test_parser_rejects_empty_evidence():
    raw = _candidate()
    raw["evidence"] = []

    with pytest.raises(ValueError, match="evidence"):
        parse_memory_candidate(raw)


def test_parser_rejects_invalid_iso_date():
    raw = _candidate(valid_from="not-a-date")

    with pytest.raises(ValueError, match="valid_from"):
        parse_memory_candidate(raw)


def test_rejects_unknown_kind():
    candidate = parse_memory_candidate(_candidate(kind="ecommerce_rule"))

    result = validate_memory_candidate(candidate, _messages())

    assert "unsupported_kind" in _codes(result)


def test_rejects_inferred_long_term_candidate():
    candidate = parse_memory_candidate(_candidate(explicitness="inferred"))

    result = validate_memory_candidate(candidate, _messages())

    assert "long_term_requires_explicit_evidence" in _codes(result)


def test_rejects_missing_evidence_message():
    candidate = parse_memory_candidate(_candidate(message_id="missing"))

    result = validate_memory_candidate(candidate, _messages())

    assert {"evidence_message_missing", "user_evidence_required"} <= _codes(result)


def test_rejects_quote_not_found_in_source_message():
    candidate = parse_memory_candidate(_candidate(quote="不存在的原话"))

    result = validate_memory_candidate(candidate, _messages())

    assert {"evidence_quote_mismatch", "user_evidence_required"} <= _codes(result)


def test_rejects_assistant_only_evidence():
    candidate = parse_memory_candidate(_candidate(
        message_id="a1",
        quote="好的，我会先给结论",
    ))

    result = validate_memory_candidate(candidate, _messages())

    assert "user_evidence_required" in _codes(result)


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        ("如果我开一家咖啡店，就用蓝色装修", "hypothetical_evidence"),
        ("比如我喜欢咖啡，你可以这样回答", "example_evidence"),
        ("这次回答先给结论", "transient_long_term_evidence"),
        ("我是不是喜欢咖啡？", "question_evidence"),
    ],
)
def test_rejects_non_factual_or_transient_user_evidence(content, expected_code):
    candidate = parse_memory_candidate(_candidate(quote=content))

    result = validate_memory_candidate(candidate, _messages(content))

    assert expected_code in _codes(result)


def test_allows_transient_evidence_for_session_scope():
    content = "这次回答先给结论"
    candidate = parse_memory_candidate(_candidate(
        scope="session",
        quote=content,
    ))

    result = validate_memory_candidate(candidate, _messages(content))

    assert result.accepted is True


def test_rejects_invalid_validity_range():
    candidate = parse_memory_candidate(_candidate(
        valid_from="2026-08-01T00:00:00+00:00",
        valid_until="2026-07-01T00:00:00+00:00",
    ))

    result = validate_memory_candidate(candidate, _messages())

    assert "invalid_validity_range" in _codes(result)


def test_rejects_duplicate_source_message_ids():
    candidate = parse_memory_candidate(_candidate())
    messages = [
        {"id": "u1", "role": "user", "content": "以后回答先给我结论"},
        {"id": "u1", "role": "user", "content": "重复消息"},
    ]

    result = validate_memory_candidate(candidate, messages)

    assert "duplicate_message_id" in _codes(result)


def test_extracts_text_from_multimodal_user_message():
    candidate = parse_memory_candidate(_candidate())
    messages = [{
        "id": "u1",
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
            {"type": "text", "text": "以后回答先给我结论"},
        ],
    }]

    result = validate_memory_candidate(candidate, messages)

    assert result.accepted is True
