"""Current restoration boundaries, previous migration evidence, and source hashes."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path


def head_bytes(path):
    return subprocess.check_output(["git", "show", f"HEAD:{path}"])


def main():
    evidence = Path("docs/document/tool-unification-evidence")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert head == "887b28ae4aedb5d4dde5f0cbbd1bcb4484fda9ba"
    unchanged = [
        "backend/services/handlers/chat/executor.py",
        "backend/services/handlers/chat/execution_engine.py",
        "backend/services/handlers/context_snapshot.py",
        "backend/services/handlers/resource_manifest.py",
        "backend/services/prompt_builder/builder.py",
        "backend/services/tools/file_calls.py", "backend/services/tools/runtime.py",
        "backend/services/tools/policy.py", "backend/services/tools/result.py",
        "backend/services/agent/agent_result.py",
        "backend/services/handlers/chat/execution_sink.py",
        "backend/services/handlers/chat/actor_sink.py",
        "backend/services/handlers/chat/tool_lifecycle.py",
        "backend/services/tool_invocation_store.py",
    ]
    for path in unchanged:
        assert Path(path).read_bytes() == head_bytes(path), path
    fixtures = list(Path("backend/tests/fixtures/tool_result_05").glob("*.json"))
    assert len(fixtures) == 18
    for path in fixtures:
        assert path.read_bytes() == head_bytes(str(path)), str(path)
    prior = json.loads((evidence / "05-items12-source-checks.json").read_text())
    reused = [p for p in prior["sha256"] if "/migrations/" in p or "test_scheduled_task_draft_delete_integration" in p]
    for path in reused:
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == prior["sha256"][path]
    schema_path = "backend/config/file_tools.py"
    namespace = {}
    exec(compile(head_bytes(schema_path), f"HEAD:{schema_path}", "exec"), namespace)
    from config.file_tools import build_file_tools
    previous, current = namespace["build_file_tools"](), build_file_tools()
    for tools in (previous, current):
        for tool in tools:
            tool["function"].pop("description")
    assert previous == current
    cache = Path("backend/services/handlers/conversation_cache.py")
    # The entire cache implementation is unchanged except its projection key.
    assert cache.read_bytes().replace(
        b'_KEY_PREFIX = "conv:msgs:outcomes-v1"  # Projection isolation; the v2 payload stays unchanged.',
        b'_KEY_PREFIX = "conv:msgs"') == head_bytes(str(cache))
    history_test = "backend/tests/test_chat_context.py"
    before = {n.name: ast.dump(n) for n in ast.walk(ast.parse(head_bytes(history_test)))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    skips = []
    for node in ast.walk(ast.parse(Path(history_test).read_text())):
        if isinstance(node, ast.ClassDef) and any(
            "V1 gather 编排" in ast.unparse(d) for d in node.decorator_list):
            assert ast.dump(node) == before[node.name]
            skips.extend(f"{node.name}.{child.name}" for child in node.body
                         if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"))
    assert len(skips) == 3
    changed = subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines()
    new = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True).splitlines()
    sources = sorted(p for p in changed + new if p.startswith("backend/") and p.endswith((".py", ".sql")))
    assert "44 failed" in (evidence / "05-context-before.txt").read_text()
    assert "986 passed, 3 skipped" in (evidence / "05-context-regression.txt").read_text()
    print(json.dumps({"version": "HEAD plus uncommitted restoration and item 1/2 changes", "head": head,
        "unchanged_boundaries": unchanged, "unchanged_protocol_fixtures": len(fixtures),
        "parameter_schemas_identical": True, "cache_payload_unchanged_only_key_isolated": True,
        "reused_postgres_8_test_evidence_sources_unchanged": reused, "unchanged_legacy_skips": skips,
        "sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources},
        "real_model_validation": "not run; no dedicated test key available; no production key loaded"},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
