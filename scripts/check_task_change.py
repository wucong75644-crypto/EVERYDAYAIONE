#!/usr/bin/env python3
"""Check mechanical quality gates for explicitly selected task files."""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path


CODE_SUFFIXES = {".py", ".sql", ".ts", ".tsx", ".js", ".jsx"}
MARKER_RE = re.compile(r"\b(?:TODO|FIXME|HACK|workaround)\b", re.IGNORECASE)
MIGRATION_RE = re.compile(r"^\d+_.+\.sql$")
MAX_FILE_LINES = 500
MAX_FUNCTION_LINES = 120


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs="+", required=True)
    return parser.parse_args()


def python_function_violations(path: Path, text: str) -> list[str]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: Python syntax error: {exc.msg} (line {exc.lineno})"]

    failures: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end_lineno = getattr(node, "end_lineno", node.lineno)
        length = end_lineno - node.lineno + 1
        if length > MAX_FUNCTION_LINES:
            failures.append(
                f"{path}:{node.lineno} function {node.name} has {length} lines "
                f"(max {MAX_FUNCTION_LINES})"
            )
    return failures


def unfinished_markers(path: Path, text: str) -> list[str]:
    if path.suffix != ".py":
        return [
            f"{path}:{number} contains unfinished marker"
            for number, line in enumerate(text.splitlines(), start=1)
            if MARKER_RE.search(line)
        ]

    failures: list[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and MARKER_RE.search(token.string):
                failures.append(
                    f"{path}:{token.start[0]} contains unfinished marker"
                )
    except tokenize.TokenError as exc:
        failures.append(f"{path}: Python tokenization error: {exc.args[0]}")
    return failures


def migration_pair_violation(root: Path, path: Path) -> str | None:
    relative = path.relative_to(root)
    if relative.parts[:2] != ("backend", "migrations"):
        return None
    if "rollback" in relative.parts or not MIGRATION_RE.match(path.name):
        return None
    rollback = path.parent / "rollback" / f"{path.stem}_rollback.sql"
    if not rollback.exists():
        return f"{path}: missing rollback {rollback.relative_to(root)}"
    return None


def check_file(root: Path, path: Path) -> list[str]:
    if not path.exists():
        return []
    if path.suffix not in CODE_SUFFIXES:
        return []

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    failures: list[str] = []
    if len(lines) > MAX_FILE_LINES:
        failures.append(f"{path}: {len(lines)} lines (max {MAX_FILE_LINES})")

    failures.extend(unfinished_markers(path, text))

    if path.suffix == ".py":
        failures.extend(python_function_violations(path, text))

    pair_failure = migration_pair_violation(root, path)
    if pair_failure:
        failures.append(pair_failure)
    return failures


def main() -> int:
    args = parse_args()
    root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    root = Path(root_result.stdout.strip())
    failures: list[str] = []
    for raw_path in args.files:
        path = (root / raw_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            failures.append(f"{raw_path}: path is outside repository")
            continue
        failures.extend(check_file(root, path))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"quality gates passed for {len(args.files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
