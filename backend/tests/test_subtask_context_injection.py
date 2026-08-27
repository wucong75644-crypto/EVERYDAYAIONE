"""子任务完成事件到下一次模型上下文的回注入测试。"""

from services.handlers.chat.execution_engine import _inject_subtask_completions


def test_subtask_completion_is_injected_as_system_event_once_by_caller() -> None:
    messages = [{"role": "user", "content": "继续"}]
    _inject_subtask_completions(messages, [{
        "child_task_id": "child-1",
        "status": "completed",
        "result": {"rows": 3},
        "error_message": "",
    }])

    assert len(messages) == 2
    assert messages[-1]["role"] == "system"
    assert "child_task_id=child-1" in messages[-1]["content"]
    assert '"rows": 3' in messages[-1]["content"]
