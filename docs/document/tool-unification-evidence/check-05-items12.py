"""Read-only boundary checks and fingerprints for the item 1/2 candidate."""
import hashlib
import json
import subprocess
from pathlib import Path


def old(path):
    return subprocess.check_output(["git", "show", f"HEAD:{path}"])


def main():
    expected = "887b28ae4aedb5d4dde5f0cbbd1bcb4484fda9ba"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert head == expected, "Rebase the comparison explicitly for a new candidate"
    unchanged = [
        "backend/services/handlers/chat/executor.py",
        "backend/services/handlers/chat/execution_engine.py",
        "backend/services/handlers/context_snapshot.py",
        "backend/services/handlers/resource_manifest.py",
        "backend/services/handlers/chat_context/history_loader.py",
        "backend/services/handlers/tool_loop_context.py",
        "backend/services/prompt_builder/builder.py",
        "backend/services/tools/file_calls.py",
        "backend/services/tools/runtime.py",
        "backend/services/tools/policy.py",
        "backend/services/tools/result.py",
        "backend/services/agent/agent_result.py",
        "backend/services/handlers/chat/execution_sink.py",
        "backend/services/handlers/chat/tool_lifecycle.py",
        "backend/services/tool_invocation_store.py",
    ]
    for path in unchanged:
        assert Path(path).read_bytes() == old(path), path
    schema_path = "backend/config/file_tools.py"
    previous = {}
    exec(compile(old(schema_path), f"HEAD:{schema_path}", "exec"), previous)
    from config.file_tools import build_file_tools
    before, after = previous["build_file_tools"](), build_file_tools()
    changed_descriptions = []
    assert len(before) == len(after)
    for left, right in zip(before, after):
        left_description = left["function"].pop("description")
        right_description = right["function"].pop("description")
        assert left == right, "Tool name/parameter schema/aliases changed"
        if left_description != right_description:
            changed_descriptions.append(right["function"]["name"])
    assert changed_descriptions == ["file_search", "file_analyze"]
    changed = [
        schema_path, "backend/config/file_call_contract.py",
        "backend/services/agent/file_describe_mixin.py",
        "backend/services/handlers/chat_context/attachments.py",
        "backend/migrations/253_scheduled_task_confirmed_draft_delete.sql",
        "backend/migrations/rollback/253_scheduled_task_confirmed_draft_delete_rollback.sql",
        "backend/tests/test_tool_result_05_production_regressions.py",
        "backend/tests/test_scheduled_task_draft_delete_integration.py",
    ]
    print(json.dumps({
        "tested_version": "HEAD plus uncommitted item 1/2 changes", "head": head,
        "unchanged_boundaries": unchanged,
        "parameter_schemas_identical": True, "changed_descriptions": changed_descriptions,
        "sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in changed},
        "grok_reference_commit": "37949780c144e37df692e3d669051a21fec24f20",
        "grok_validation": "official source comparison only; not compiled or run",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
