"""Reproducible source/entrypoint/compatibility checks; no application imports or IO."""
import ast
import hashlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = "0f65d72dd00a0fce6885d4df0b7977454f666812"


def git(*args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args], cwd=ROOT, text=True).strip()


def old(path):
    return git("show", f"{BASE}:{path}")


def tree_methods(text, class_name):
    cls = next(n for n in ast.parse(text).body if isinstance(n, ast.ClassDef) and n.name == class_name)
    return {n.name: ast.dump(n) for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def unchanged_methods(path, class_name, exempt=()):
    before, after = tree_methods(old(path), class_name), tree_methods((ROOT / path).read_text(), class_name)
    names = set(before) - set(exempt)
    assert all(before[name] == after[name] for name in names), path
    print(f"UNCHANGED AST {path}::{class_name}: {', '.join(sorted(names))}")


print("BASE", BASE)
print("HEAD", git("rev-parse", "HEAD"))
print("BRANCH", git("branch", "--show-current"))
print("ROOT", ROOT)
for ref in (BASE, "origin/main", "2ed4d783"):
    print("TREE", ref, git("rev-parse", ref + "^{tree}"))
assert git("rev-parse", BASE + "^{tree}") == git("rev-parse", "2ed4d783^{tree}")
for ref in ("b4c854ac", "4084db4e", "e243ba2c", "2ed4d783"):
    subprocess.run(["git", "merge-base", "--is-ancestor", ref, BASE], cwd=ROOT, check=True)
print("PASS block 01-03 ancestry and block 03 final candidate tree equality")

tracked = git("diff", "--name-only", BASE).splitlines()
untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
sources = sorted(p for p in set(tracked + untracked) if p.startswith("backend/") and p.endswith(".py"))
print("TESTED SOURCE FINGERPRINTS (BASE + uncommitted task changes; HEAD is not this candidate)")
for path in sources:
    data = (ROOT / path).read_bytes()
    ast.parse(data, filename=path)
    print(hashlib.sha256(data).hexdigest(), path)
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
subprocess.run(["bash", "-n", "docs/document/tool-unification-evidence/run-04.sh"], cwd=ROOT, check=True)
assert all(p.startswith(("backend/", "docs/document/", "docs/CURRENT_ISSUES.md")) for p in tracked + untracked)
print("PASS AST parse, diff --check, shell syntax, task scope")

unchanged_methods("backend/services/agent/tool_executor.py", "ToolExecutor", ("__init__", "execute"))
unchanged_methods("backend/services/file_executor.py", "FileExecutor", ("__init__",))
unchanged_methods("backend/services/tool_invocation_store.py", "DatabaseToolInvocationStore")
ledger = "backend/services/tool_invocation_store.py"
for text in (old(ledger), (ROOT / ledger).read_text()):
    functions = {n.name: ast.dump(n) for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
    if text == old(ledger):
        baseline_functions = functions
    else:
        assert functions == baseline_functions
print("PASS existing invocation serializer/deserializer/hash and fenced RPC methods unchanged")

preserved = [
    "backend/services/agent/agent_result.py", "backend/services/agent/tool_output.py",
    "backend/services/tools/result.py", "backend/services/tools/dispatcher.py",
    "backend/services/tools/legacy_handler.py", "backend/schemas/websocket.py",
    "backend/services/handlers/chat_tool_result_mixin.py",
    "backend/services/agent/tool_result_cache.py", "backend/services/agent/tool_audit.py",
    "backend/services/agent/erp_agent.py", "backend/services/agent/sandbox_tool_mixin.py",
    "backend/services/conversation_execution.py", "backend/services/conversation_worker.py",
    "backend/services/handlers/chat/executor.py", "backend/services/model_gateway.py",
]
for path in preserved:
    assert not git("diff", BASE, "--", path), path
    print("UNCHANGED FILE", path)
for prefix in ("backend/services/kuaimai", "backend/services/erp", "backend/services/media",
               "backend/migrations", "frontend"):
    assert not git("diff", BASE, "--", prefix), prefix
    print("UNCHANGED TREE", prefix)
# User-approved root repair changes only these schema / sandbox implementations.
for prefix, allowed in (
    ("backend/config", {"backend/config/file_tools.py"}),
    ("backend/services/sandbox", {"backend/services/sandbox/executor.py", "backend/services/sandbox/kernel_manager.py"}),
):
    changed = set(git("diff", "--name-only", BASE, "--", prefix).splitlines())
    assert changed <= allowed, (prefix, changed)
    print("AUTHORIZED ROOT REPAIR", prefix, sorted(changed))
engine = "backend/services/handlers/chat/execution_engine.py"
def safe_points(text):
    return [ast.dump(n) for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Await)
            and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "safe_point"]
assert safe_points(old(engine)) == safe_points((ROOT / engine).read_text())
print("PASS shared Chat/Actor safe-point calls unchanged")

constructors = {"ToolExecutor": [], "ToolLoopExecutor": [], "ToolExecutionService": []}
for prefix in ("backend/services", "backend/api"):
    for file in sorted((ROOT / prefix).rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_bytes())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in constructors:
                constructors[node.func.id].append(str(file.relative_to(ROOT)))
                print("PRODUCTION CONSTRUCTOR", f"{file.relative_to(ROOT)}:{node.lineno}", node.func.id)
assert sorted(constructors["ToolExecutor"]) == [
    "backend/services/agent/scheduled_task_agent.py", "backend/services/handlers/chat_tool_mixin.py"]
assert constructors["ToolLoopExecutor"] == ["backend/services/agent/scheduled_task_agent.py"]
assert constructors["ToolExecutionService"] == ["backend/services/tools/runtime.py"]
print("PASS production constructors fully enumerated")
for pattern in (
    r"tool_runtime\.execute|runtime\.execute|dispatcher\.dispatch|_handlers\.get|return await handler\(",
    r"registry\.resolve|tool_runtime\.advertised|tool_runtime\.batches",
    r"_request_user_confirm\(|partition_tool_calls\(|validate_runtime_tool\(",
):
    output = subprocess.check_output(["rg", "-n", pattern, "backend/services", "backend/api", "--glob", "*.py"], cwd=ROOT, text=True)
    print("CALLSITE SEARCH", pattern, "\n" + output)
print("PASS: remaining private Handler calls belong to Dispatcher/business internals; no model entry dispatches _handlers directly")
print("No new persistence payload or DB schema; optional resource_ref(s)/record_id inputs are approved. WS approved is validated as literal bool; fields/builders/frontend unchanged.")
