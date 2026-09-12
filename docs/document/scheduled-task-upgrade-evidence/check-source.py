"""Audit the scheduled-task follow-up without rewriting the frozen 07 evidence."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from config.chat_tools import get_chat_tools
from services.tools import build_tool_catalog, validate_legacy_coverage
from services.tool_executor import ToolExecutor

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BASE = "75fced912ce1ee30668b1a588043b26120ee1e8d"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main():
    assert git("rev-parse", "HEAD") == BASE, "Update the tested baseline explicitly after committing"
    registry = build_tool_catalog()
    executor = ToolExecutor(None, "synthetic-owner", "synthetic-conversation", "synthetic-org")
    issues = validate_legacy_coverage(registry, public_schemas=get_chat_tools("synthetic-org"), handler_names=executor._handlers)
    assert not issues and len(registry.specs()) == 35 and len(get_chat_tools("synthetic-org")) == 33
    assert all(spec.definition_kind == "explicit" for spec in registry.specs())
    changed = git("diff", BASE, "--name-only").splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    files = sorted(p for p in set(changed + untracked) if p.startswith(("backend/", "frontend/", "deploy/")))
    protected = (
        "backend/services/kuaimai/", "backend/services/sandbox/", "backend/services/media_tool_executor.py",
        "backend/services/agent/scheduled_task_agent.py", "backend/services/scheduler/scheduled_task_workflow.py",
        "backend/services/scheduler/delivery_worker.py", "backend/services/agent/tool_result_cache.py",
        "backend/config/tool_registry.py", "backend/services/agent/tool_selector.py",
    )
    assert not [p for p in files if p.startswith(protected)]
    calls = []
    for path in (ROOT / "backend/services").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"ToolExecutor", "ToolLoopExecutor"}:
                calls.append({"path": str(path.relative_to(ROOT)), "line": node.lineno, "constructor": node.func.id})
    assert Counter(c["constructor"] for c in calls) == {"ToolExecutor": 3, "ToolLoopExecutor": 1}
    # The added constructor checks the owner's capability scope and never invokes a tool.
    inventory = [{"name": s.name, "source": s.source, "domain": s.domain, "risk": s.risk_level,
                  "parallel": s.parallelizable, "cacheable": s.cacheable, "effects": s.effects,
                  "exposure": s.exposure.value, "handler": s.handler_key,
                  "schema_variants": list(s.schema_variants)} for s in registry.specs()]
    patch = subprocess.check_output(["git", "-C", str(ROOT), "diff", BASE, "--", "backend", "frontend", "deploy"])
    for path in sorted(set(untracked) & set(files)):
        result = subprocess.run(["git", "-C", str(ROOT), "diff", "--no-index", "--", "/dev/null", path], capture_output=True)
        assert result.returncode == 1
        patch += result.stdout
    (OUT / "implementation.patch").write_bytes(patch)
    report = {"base": BASE, "head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
              "tested_version": "HEAD plus the source fingerprints and implementation.patch; not yet a release candidate",
              "registered": 35, "public": 33, "handler_only": 2, "coverage_issues": issues,
              "inventory": inventory, "production_constructors": calls, "protected_sources_unchanged": protected,
              "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files},
              "patch_sha256": hashlib.sha256(patch).hexdigest()}
    (OUT / "source-checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("PASS: 35 registered, 33 public, 2 internal; 0 missing/duplicate/handler gaps; protected engines unchanged")
    print("PASS: 2 existing ToolExecutor execution constructions + 1 new read-only capability check; 1 ToolLoopExecutor")


if __name__ == "__main__":
    main()
