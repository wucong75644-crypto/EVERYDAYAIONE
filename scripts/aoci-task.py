#!/usr/bin/env python3
"""Bind AOCI to a task checkout; report stable updates without editing task code."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

VERSION = "0.1.0-rc17"
MARKER = "# everydayai:aoci-mcp:v1"
FORMAL = ("aoci.txt", "aoci.meta.txt", "aoci.code.txt", ".aoci/.gitignore",
          ".aoci/config.json", ".aoci/baseline.json")


def run(args, root, check=True):
    return subprocess.run(args, cwd=root, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check)


def git(root, *args, check=True):
    return run(["git", "-C", str(root), *args], root, check)


def setting(root, key):
    return git(root, "config", "--get", key, check=False).stdout.strip()


def binary(root):
    value = os.environ.get("AOCI_BINARY") or setting(root, "codex.aociBinary")
    if not value:
        value = str(Path.home() / ".local/share/aoci" / VERSION / "aoci")
    path = Path(value)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("AOCI_BINARY 必须指向已校验的绝对可执行路径：" + value)
    identity = run([str(path), "--version"], root).stdout
    if not re.match(r"aoci version " + re.escape(VERSION) + r"(?:\s|$)", identity):
        raise ValueError("AOCI 版本不匹配，要求 " + VERSION)
    return path.resolve()


def enabled(root):
    # Missing one asset must not silently turn governance off.
    return ((root / ".aoci").exists() or any((root / p).exists() for p in FORMAL)
            or bool(git(root, "ls-files", "--", *FORMAL).stdout))


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        f.write(text)
        name = f.name
    try:
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare(root):
    if not enabled(root):
        return {"status": "not_enabled"}
    executable = binary(root)
    config = root / ".codex/config.toml"
    text = config.read_text() if config.exists() else ""
    block = (MARKER + "\n[mcp_servers.aoci]\ncommand = " + json.dumps(str(executable))
             + "\nargs = " + json.dumps(["--repo", str(root), "mcp"]) + "\n")
    if config.is_symlink() or config.parent.is_symlink():
        raise ValueError("拒绝写入符号链接配置")
    parsed = tomllib.loads(text)
    existing = parsed.get("mcp_servers", {}).get("aoci")
    pattern = re.compile(r"(?ms)^" + re.escape(MARKER)
                         + r"\n\[mcp_servers\.aoci\]\n.*?(?=^\[|\Z)")
    matches = list(pattern.finditer(text))
    if existing is not None and not matches:
        if (existing.get("command") != str(executable)
                or existing.get("args") != ["--repo", str(root), "mcp"]
                or existing.get("enabled") is False):
            raise ValueError("已有非托管 AOCI 配置，拒绝覆盖；请核对 command/args/enabled")
        return {"status": "configured", "root": str(root), "binary": str(executable)}
    if len(matches) > 1:
        raise ValueError("AOCI 配置存在重复托管块")
    if existing is not None and existing.get("enabled") is False:
        raise ValueError("AOCI 已被用户禁用，拒绝自动重新启用")
    if matches:
        old_block = matches[0].group()
        command_line = 'command = ' + json.dumps(str(executable))
        args_line = 'args = ' + json.dumps(["--repo", str(root), "mcp"])
        if (len(re.findall(r"(?m)^command =.*$", old_block)) != 1
                or len(re.findall(r"(?m)^args =.*$", old_block)) != 1):
            raise ValueError("托管配置格式已由用户改变，拒绝覆盖")
        updated = re.sub(r"(?m)^command =.*$", lambda _: command_line, old_block)
        updated = re.sub(r"(?m)^args =.*$", lambda _: args_line, updated)
        new_text = text[:matches[0].start()] + updated + text[matches[0].end():]
    else:
        new_text = text + ("\n" if text.endswith("\n") else "\n\n") + block
    if new_text != text:
        if config.is_symlink():
            raise ValueError("拒绝写入符号链接配置")
        if config.exists():
            # Retain the previous local config, outside Git/managed scope.
            atomic_write(config.with_suffix(".toml.aoci-backup"), text)
        atomic_write(config, new_text)
    return {"status": "configured", "root": str(root), "binary": str(executable),
            "reload": "若当前对话未暴露 AOCI MCP 工具，请重新打开本工作树对话"}


def verify(root):
    if not enabled(root):
        return {"status": "not_enabled"}
    missing = [p for p in FORMAL if not (root / p).is_file()]
    if missing:
        raise ValueError("AOCI 正式资产缺失：" + ", ".join(missing))
    executable = binary(root)
    result = run([str(executable), "--repo", str(root), "verify", "--json"], root,
                 check=False)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise ValueError("AOCI verify 未返回有效 JSON；请运行 live Guide 诊断")
    # Verify's read_only_candidate describes the verification candidate, not
    # unresolved drift. The CLI's exit status and governance facts are the gate.
    if (result.returncode or report.get("structure_valid") is not True
            or report.get("governance_aligned") is not True):
        raise ValueError("AOCI 尚未 aligned；通过当前 MCP Guide 完成索引维护后再发布")
    check = run([str(executable), "--repo", str(root), "check", "--json"], root,
                check=False)
    if check.returncode:
        raise ValueError("AOCI check 未通过；请运行 live Guide 诊断")
    return {"status": "aligned", "root": str(root), "version": VERSION}


def session(root):
    stable = setting(root, "codex.aociStableCommit") or setting(root, "codex.taskStableBase")
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if stable:
        git(root, "rev-parse", "--verify", stable + "^{commit}")
    pending = bool(stable and git(root, "merge-base", "--is-ancestor", stable, "HEAD",
                                 check=False).returncode != 0)
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=all").stdout)
    result = {"root": str(root), "head": head, "stable_commit": stable,
              "stable_update_pending": pending, "dirty": dirty}
    if pending:
        result.update(status="stable_update_pending", action=(
            "保留当前任务修改；检查稳定版本差异，在安全检查点合入对应提交，"
            "处理冲突后通过 MCP 维护索引并重新读取 Overview；不得单独复制索引"))
        return result
    result.update(prepare(root))
    if enabled(root):
        try:
            result.update(verify(root))
        except ValueError as error:
            result.update(status="maintenance_required", action=str(error))
        result["cognition"] = "核验 MCP runtime_repository_root，再读取 Rules/live Guide 和完整 Overview；脚本不能证明模型已理解"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "session", "verify"])
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        root = Path(args.repo).resolve()
        actual = Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        if root != actual:
            raise ValueError("--repo 必须是本任务工作树根目录")
        result = {"prepare": prepare, "session": session, "verify": verify}[args.command](root)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 2 if result.get("status") in ("stable_update_pending", "maintenance_required") else 0


if __name__ == "__main__":
    sys.exit(main())
