from types import SimpleNamespace
from unittest.mock import Mock

from services.conversation_control_router import ControlAction
from services.conversation_control_service import (
    execute_control_action,
    load_control_tasks,
)


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _Query(self.rows)


def _task(*, status="running", org_id="org-1", actor=True, task_id="task-1"):
    return {
        "id": task_id,
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "org_id": org_id,
        "type": "chat",
        "status": status,
        "delivery_context": {"actor": actor},
        "client_task_id": f"client-{task_id}",
        "external_task_id": f"external-{task_id}",
        "assistant_message_id": f"message-{task_id}",
    }


def test_load_control_tasks_keeps_actor_and_tenant_scope():
    db = _DB([
        _task(task_id="wrong-org", org_id="org-2"),
        _task(task_id="legacy", actor=False),
        _task(task_id="running", status="running"),
        _task(task_id="paused", status="paused"),
    ])

    tasks = load_control_tasks(
        db, conversation_id="conv-1", user_id="user-1", org_id="org-1",
    )

    assert tasks.running["id"] == "running"
    assert tasks.paused["id"] == "paused"
    assert tasks.state == "running"


def test_execute_pause_delegates_to_existing_actor_control(monkeypatch):
    task = _task()
    tasks = load_control_tasks(
        _DB([task]), conversation_id="conv-1", user_id="user-1", org_id="org-1",
    )
    pause = Mock(return_value={"outcome": "requested"})
    monkeypatch.setattr(
        "services.conversation_control_service.pause_actor_task", pause,
    )

    result = execute_control_action(
        _DB([]), tasks=tasks, action=ControlAction.PAUSE,
        user_id="user-1", org_id="org-1",
    )

    pause.assert_called_once()
    assert result["action"] == "pause"
    assert result["outcome"] == "requested"
    assert result["client_task_id"] == "client-task-1"
