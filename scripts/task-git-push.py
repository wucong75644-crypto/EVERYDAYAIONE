#!/usr/bin/env python3
"""Commit an explicit task file list and verify origin's branch SHA."""
import argparse
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


class PushError(RuntimeError):
    pass


def git(*args, capture=True):
    result = subprocess.run(["git", "--literal-pathspecs", *args], capture_output=capture)
    if result.returncode:
        raise PushError(f"Git operation failed: {args[0]} (exit {result.returncode}); preserve current state and retry explicitly")
    return result.stdout if capture else b""


def paths(data):
    return {os.fsdecode(p) for p in data.split(b"\0") if p}


def validate_path(value, *, retry=False):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise PushError(f"Invalid file path: {value}")
    forbidden = {".git", ".cursor", ".codex", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".cloud-dev", "outputs", "tmp", "coverage", "htmlcov", "dist"}
    if any(p in forbidden or p.startswith(".env") for p in path.parts):
        raise PushError(f"Forbidden file path: {value}")
    if value == "deploy/config.env" or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".pyc"} or path.name in {"id_rsa", "id_ed25519", ".coverage"}:
        raise PushError(f"Forbidden credential/output path: {value}")
    if Path(value).is_dir() or any(p.is_symlink() for p in [Path(value), *Path(value).parents]):
        raise PushError(f"Expected a literal non-symlink file: {value}")
    tracked = paths(git("ls-files", "-z", "--", value))
    if tracked and tracked != {value}:
        raise PushError(f"Expected an individual tracked file: {value}")
    if not Path(value).exists() and not tracked and not retry:
        raise PushError(f"File not found or tracked: {value}")


def inspect_diff(data):
    # Defense in depth; path checks and human diff review remain necessary.
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bsk-[A-Za-z0-9_-]{32,}", line):
            raise PushError("Possible credential in added content; inspect locally before committing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--message")
    mode.add_argument("--push-commit")
    parser.add_argument("--file", action="append", required=True)
    args = parser.parse_args()
    root = os.fsdecode(git("rev-parse", "--show-toplevel")).strip()
    os.chdir(root)
    branch = os.fsdecode(git("symbolic-ref", "--quiet", "--short", "HEAD")).strip()
    if not branch.startswith("codex/task/"):
        raise PushError("Only codex/task/* task branches may be pushed")
    main_tree = os.fsdecode(git("worktree", "list", "--porcelain")).splitlines()[0].removeprefix("worktree ")
    if Path(root).resolve() == Path(main_tree).resolve():
        raise PushError("Refusing to commit from the primary worktree")
    if git("diff", "--cached", "--name-only", "-z"):
        raise PushError("Existing staged changes: preserve them and resolve explicitly")
    if git("ls-files", "-u", "-z"):
        raise PushError("Unresolved merge conflicts")
    selected = set(args.file)
    for value in selected:
        validate_path(value, retry=bool(args.push_commit))
    if args.push_commit:
        sha = os.fsdecode(git("rev-parse", "--verify", args.push_commit + "^{commit}")).strip()
        if sha != os.fsdecode(git("rev-parse", "HEAD")).strip():
            raise PushError("Retry commit must equal current HEAD")
        if len(git("rev-list", "--parents", "-n", "1", sha).split()) != 2:
            raise PushError("Retry requires a non-merge commit with one parent")
        changed = paths(git("diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", sha))
        if not changed or not changed <= selected:
            raise PushError("Retry commit contains files outside explicit selection")
        inspect_diff(git("show", "--format=", "--no-ext-diff", sha))
    else:
        if not args.message.strip():
            raise PushError("Commit message cannot be empty")
        # Every path is validated before the index is touched. Literal pathspecs
        # prevent wildcard or pathspec-magic expansion, including tracked deletions.
        git("add", "--", *sorted(selected))
        changed = paths(git("diff", "--cached", "--name-only", "--no-renames", "-z"))
        if not changed or not changed <= selected:
            raise PushError("No selected changes or staged files outside selection; preserve index for inspection")
        inspect_diff(git("diff", "--cached", "--no-ext-diff"))
        git("diff", "--cached", "--check")
        git("commit", "-m", args.message, capture=False)
        sha = os.fsdecode(git("rev-parse", "HEAD")).strip()
        actual = paths(git("diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", sha))
        if actual != changed or git("diff", "--cached", "--name-only", "-z"):
            raise PushError("Commit hooks changed the file scope; commit retained but not pushed")
    print(f"TASK_COMMIT branch={branch} sha={sha}", flush=True)
    git("push", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}", capture=False)
    remote = git("ls-remote", "--exit-code", "origin", f"refs/heads/{branch}").split()
    if not remote or remote[0].decode() != sha:
        raise PushError("Remote SHA mismatch; preserve commit and inspect origin")
    print(f"TASK_PUSH_RESULT status=success branch={branch} local_sha={sha} remote_sha={sha}")


if __name__ == "__main__":
    try:
        main()
    except PushError as exc:
        print(f"TASK_PUSH_ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
