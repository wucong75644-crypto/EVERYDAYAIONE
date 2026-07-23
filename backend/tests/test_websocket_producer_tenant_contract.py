"""WebSocket 消息生产者必须显式声明租户上下文。"""

import ast
from pathlib import Path


TENANT_ROUTED_METHODS = {
    "send_to_user",
    "send_to_task_subscribers",
    "send_to_task_or_user",
}


def test_all_production_websocket_producers_pass_org_id():
    backend_dir = Path(__file__).parent.parent
    missing: list[str] = []

    for path in backend_dir.rglob("*.py"):
        if "tests" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in TENANT_ROUTED_METHODS:
                continue
            if not any(keyword.arg == "org_id" for keyword in node.keywords):
                missing.append(
                    f"{path.relative_to(backend_dir)}:{node.lineno}:"
                    f"{node.func.attr}"
                )

    assert missing == []
