"""WebSocket 任务订阅租户范围测试。"""

from dataclasses import dataclass
from typing import Any

from services.websocket_task_scope import find_task_in_connection_scope


@dataclass
class _Result:
    data: dict[str, Any] | None


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._filters: list[tuple[str, Any]] = []

    def select(self, _fields: str) -> "_Query":
        return self

    def eq(self, field: str, value: Any) -> "_Query":
        self._filters.append((field, value))
        return self

    def is_(self, field: str, value: str) -> "_Query":
        self._filters.append((field, None if value == "null" else value))
        return self

    def maybe_single(self) -> "_Query":
        return self

    def execute(self) -> _Result:
        matches = [
            row for row in self._rows
            if all(row.get(field) == value for field, value in self._filters)
        ]
        return _Result(matches[0] if matches else None)


class _DB:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def table(self, name: str) -> _Query:
        assert name == "tasks"
        return _Query(self._rows)


TASKS = [
    {
        "id": "task-org-a",
        "client_task_id": "client-org-a",
        "external_task_id": None,
        "user_id": "user-1",
        "org_id": "org-a",
    },
    {
        "id": "task-org-b",
        "client_task_id": "client-org-b",
        "external_task_id": None,
        "user_id": "user-1",
        "org_id": "org-b",
    },
    {
        "id": "task-personal",
        "client_task_id": "client-personal",
        "external_task_id": None,
        "user_id": "user-1",
        "org_id": None,
    },
]


def test_finds_task_in_matching_enterprise_scope() -> None:
    task = find_task_in_connection_scope(
        _DB(TASKS), "client-org-a", "user-1", "org-a",
    )
    assert task is not None
    assert task["id"] == "task-org-a"


def test_rejects_same_user_task_from_other_enterprise() -> None:
    task = find_task_in_connection_scope(
        _DB(TASKS), "client-org-b", "user-1", "org-a",
    )
    assert task is None


def test_personal_connection_only_finds_personal_task() -> None:
    personal = find_task_in_connection_scope(
        _DB(TASKS), "client-personal", "user-1", None,
    )
    enterprise = find_task_in_connection_scope(
        _DB(TASKS), "client-org-a", "user-1", None,
    )
    assert personal is not None
    assert personal["id"] == "task-personal"
    assert enterprise is None


def test_rejects_task_owned_by_another_user() -> None:
    task = find_task_in_connection_scope(
        _DB(TASKS), "client-org-a", "user-2", "org-a",
    )
    assert task is None
