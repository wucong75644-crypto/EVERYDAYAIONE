"""Reproducible block 03 source/scope checks, with no service configuration or IO."""

import ast
from collections import Counter
import hashlib
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[3]
BASE = "8e74f57de1073cbef8b3b8c8256e4409d60c506b"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main():
    lines = [f"BASE {BASE}", f"HEAD {git('rev-parse', 'HEAD')}",
             f"BRANCH {git('branch', '--show-current')}"]
    assert git("branch", "--show-current") == "codex/task/20260909195904-tool-unification-03"
    for candidate, merge in [("b4c854ac", "2e8fdb2d"), ("4084db4e", BASE)]:
        subprocess.run(["git", "merge-base", "--is-ancestor", candidate, BASE], cwd=ROOT, check=True)
        assert git("rev-parse", candidate + "^{tree}") == git("rev-parse", merge + "^{tree}")
        lines.append(f"PREDECESSOR {candidate} ancestor=True merge={merge} equal_tree={git('rev-parse', merge + '^{tree}')}")

    tracked = git("diff", BASE, "--name-only").splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    backend_changes = [p for p in tracked + untracked if p.startswith("backend/")]
    expected = {
        "backend/services/tools/__init__.py", "backend/services/tools/dispatcher.py",
        "backend/services/tools/execution.py", "backend/services/tools/legacy_handler.py",
        "backend/services/tools/result.py", "backend/tests/test_tool_execution.py", "backend/tests/test_tool_result.py",
    }
    assert set(backend_changes) == expected
    lines.extend([f"TRACKED_DIFF {tracked}", f"UNTRACKED {untracked}"])
    assert not git("diff", BASE, "--", "backend/services/agent", "backend/services/handlers", "backend/config",
                   "backend/services/scheduler", "backend/schemas", "backend/services/tool_invocation_store.py")
    assert not git("diff", BASE, "--", "backend/tests")
    lines += ["PRODUCTION_SOURCE_DIFF EMPTY", "EXISTING_TEST_DIFF EMPTY"]

    reverse = []
    for folder in ("backend/services", "backend/config"):
        for path in (ROOT / folder).rglob("*.py"):
            if "services/tools" in path.as_posix():
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("services.tools"):
                    reverse.append(str(path.relative_to(ROOT)))
                if isinstance(node, ast.Import) and any(alias.name.startswith("services.tools") for alias in node.names):
                    reverse.append(str(path.relative_to(ROOT)))
    assert not reverse, reverse
    lines.append("PRODUCTION_REVERSE_IMPORTS NONE")

    for name in ("dispatcher.py", "legacy_handler.py"):
        tree = ast.parse((ROOT / "backend/services/tools" / name).read_text())
        assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr == "execute" for n in ast.walk(tree))
    lines.append("DISPATCHER_LEGACY_PUBLIC_EXECUTE_CALLS NONE")
    for path in sorted((ROOT / "backend/services/tools").glob("*.py")) + [ROOT / p for p in sorted(expected) if p.startswith("backend/tests")]:
        data = path.read_bytes()
        source = data.decode()
        ast.parse(source)
        assert all(line == line.rstrip() for line in source.splitlines()), path
        lines.append(f"SHA256 {hashlib.sha256(data).hexdigest()} {path.relative_to(ROOT)}")
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    subprocess.run(["bash", "-n", "docs/document/tool-unification-evidence/run-03.sh"], cwd=ROOT, check=True)
    lines += ["PYTHON_AST_WHITESPACE PASS", "DIFF_CHECK PASS", "BASH_SYNTAX PASS"]

    for filename, count in [("03-isolated.txt", 740), ("03-regression.txt", 720), ("03-regression-erp.txt", 11)]:
        content = (ROOT / "docs/document/tool-unification-evidence" / filename).read_text()
        summary = content.splitlines()[-1]
        assert f"{count} passed" in summary
        assert not any(word in summary for word in ("failed", "error", "skipped"))
        lines.append(f"RESULT {filename} {summary}")
        if filename == "03-isolated.txt":
            counts = Counter(line.split("::")[0] for line in content.splitlines() if " PASSED " in line)
            lines.append(f"ISOLATED_COUNTS {dict(counts)}")

    for filename in ("TOOL_UNIFICATION_HANDOFF.md", "TOOL_UNIFICATION_ACCEPTANCE_03.md", "TOOL_UNIFICATION_EXECUTION_03.md"):
        path = ROOT / "docs/document" / filename
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            destination = path.parent / target.split("#")[0]
            # This report is written below in this same invocation.
            assert destination.name == "03-source-checks.txt" or destination.exists(), target
    lines.append("HANDOFF_ACCEPTANCE_INTERFACE_LINKS PASS")
    lines.append("REVIEW: canonical policy before dispatch; zero handler calls on denial; bound internal callables; no UI/business code in dispatcher; returned business failures vs unknown effects; no retries; cancellation propagation; lazy identity-preserving projections; production isolation checked.")
    lines.append("LIMITS: request-local consumption only; trusted context/executor pairing remains with server adapter; no real external deletion/generation, production switch, persistence payload, deployment or user acceptance.")
    report = "\n".join(lines) + "\n"
    (ROOT / "docs/document/tool-unification-evidence/03-source-checks.txt").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
