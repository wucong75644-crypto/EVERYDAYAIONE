"""Chat 执行预算终止原因的用户提示。"""


def stop_message(reason: str) -> str:
    messages = {
        "wrap_up_budget": "接近执行上限，正在总结当前进展。",
        "max_turns": "已达到单次对话工具调用上限。",
        "wall_timeout": "任务耗时过长，请稍后重试。",
    }
    return messages.get(reason, reason)
