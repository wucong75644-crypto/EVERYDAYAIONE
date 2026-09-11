"""Compare the historical projection change using synthetic, offline inputs.

Loads only the target pure function from each Git revision. Its extractor
dependency is asserted identical across those revisions. Does not run old apps,
connect to services, or treat this reproduction as proof of model causation.
"""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

from services.handlers.chat_context import content_extractors as extractors


def show(revision, path):
    return subprocess.check_output(["git", "show", f"{revision}:{path}"])


def main():
    change = "c4053fef7999e28fecafde0f00a4bffffa7eca8f"
    deployed = "887b28ae4aedb5d4dde5f0cbbd1bcb4484fda9ba"
    history = "backend/services/handlers/chat_context/history_loader.py"
    extractor = "backend/services/handlers/chat_context/content_extractors.py"
    revisions = [f"{change}^", change, deployed]
    current_dependency = Path(extractor).read_bytes()
    row = {"role": "assistant", "status": "completed", "content": [
        {"type": "tool_step", "status": "completed", "tool_name": "manage_scheduled_task",
         "tool_call_id": "synthetic-form-call", "input": {"action": "create"},
         "output": "表单已展示"},
        {"type": "form", "form_id": "synthetic-form", "status": "open"},
    ]}
    results = {}
    for revision in revisions:
        assert show(revision, extractor) == current_dependency
        source = show(revision, history)
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_row_to_oai_messages")
        namespace = {name: getattr(extractors, name) for name in (
            "extract_text_from_content", "extract_oai_messages_from_content",
            "extract_image_urls_from_content")}
        # Postponed annotations avoid importing any historical application setup.
        prefix = ast.parse("from __future__ import annotations").body
        module = ast.fix_missing_locations(ast.Module(body=prefix + [function], type_ignores=[]))
        exec(compile(module, f"{revision}:{history}", "exec"), namespace)
        messages, image_count = namespace["_row_to_oai_messages"](row, 0)
        assert image_count == 0
        results[revision] = {"source_sha256": hashlib.sha256(source).hexdigest(),
                             "messages": messages}
    before = results[revisions[0]]["messages"]
    assert [m["role"] for m in before] == ["assistant", "tool"]
    assert before[1]["content"] == "表单已展示"
    assert results[change]["messages"] == results[deployed]["messages"] == []
    assert Path(history).read_bytes() == show(deployed, history)
    print(json.dumps({"purpose": "historical diagnosis; not a context fix or model test",
                      "extractor_identical": True, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
