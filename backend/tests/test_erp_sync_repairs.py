from services.kuaimai.erp_sync_utils import _normalize_archive_rows


def test_normalize_archive_rows_converts_legacy_json_array():
    rows = _normalize_archive_rows([
        {"id": "1", "exception_tags": '["EX_INSUFFICIENT"]'},
    ])

    assert rows == [{"id": "1", "exception_tags": '{"EX_INSUFFICIENT"}'}]


def test_normalize_archive_rows_keeps_native_array_and_parses_pg_literal():
    rows = _normalize_archive_rows([
        {"exception_tags": ["A", "B"]},
        {"exception_tags": '{"A","B"}'},
    ])

    assert rows[0]["exception_tags"] == '{"A","B"}'
    assert rows[1]["exception_tags"] == '{"A","B"}'


def test_normalize_archive_rows_escapes_text_array_values():
    rows = _normalize_archive_rows([
        {"exception_tags": ['a,b', 'say "hi"', "a\\b", None]},
        {"exception_tags": []},
    ])

    assert rows[0]["exception_tags"] == '{"a,b","say \\"hi\\"","a\\\\b",NULL}'
    assert rows[1]["exception_tags"] == "{}"
