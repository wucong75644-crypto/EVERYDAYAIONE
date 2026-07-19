#!/bin/bash
# Synced from Claude PostToolUse Edit hooks: prettier + tsc hint + console.log warning
input=$(cat)
HOOK_INPUT="$input" python3 - <<'PY'
import json, os, re, subprocess
from pathlib import Path

raw = os.environ.get("HOOK_INPUT", "")
try:
    d = json.loads(raw)
except Exception:
    d = {}

file_path = (
    d.get("file_path")
    or d.get("path")
    or (d.get("tool_input") or {}).get("file_path")
    or ""
)
if not file_path or not Path(file_path).is_file():
    raise SystemExit(0)

path = Path(file_path)
msgs = []

if path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
    try:
        subprocess.run(
            ["npx", "prettier", "--write", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass

if path.suffix in {".ts", ".tsx"}:
    dir_path = path.parent
    while dir_path != dir_path.parent and not (dir_path / "tsconfig.json").exists():
        dir_path = dir_path.parent
    if (dir_path / "tsconfig.json").exists():
        try:
            r = subprocess.run(
                ["npx", "tsc", "--noEmit", "--pretty", "false"],
                cwd=str(dir_path),
                capture_output=True,
                text=True,
                check=False,
            )
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            hits = [ln for ln in out.splitlines() if str(path) in ln][:10]
            if hits:
                msgs.append("\n".join(hits))
        except Exception:
            pass

if path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        matches = [f"{i}: {ln.strip()}" for i, ln in enumerate(lines, 1) if "console.log" in ln]
        if matches:
            msgs.append(
                f"[Hook] WARNING: console.log found in {path}\n"
                + "\n".join(matches[:5])
                + "\n[Hook] Remove console.log before committing"
            )
    except Exception:
        pass

if msgs:
    print(json.dumps({"additional_context": "\n\n".join(msgs)}, ensure_ascii=False))
PY
exit 0
