#!/bin/bash
# Synced from Claude PreToolUse: suggest tmux for long-running package/test commands
input=$(cat)
HOOK_INPUT="$input" python3 - <<'PY'
import json, os, re

raw = os.environ.get("HOOK_INPUT", "")
try:
    d = json.loads(raw)
except Exception:
    d = {}
cmd = d.get("command") or d.get("tool_input", {}).get("command") or ""

in_tmux = bool(os.environ.get("TMUX"))
pat = re.compile(
    r"(npm (install|test)|pnpm (install|test)|yarn (install|test)?|bun (install|test)|cargo build|\bmake\b|\bdocker\b|\bpytest\b|\bvitest\b|\bplaywright\b)"
)
if (not in_tmux) and pat.search(cmd):
    print(json.dumps({
        "permission": "allow",
        "agent_message": "[Hook] Consider running in tmux for session persistence. tmux new -s dev  |  tmux attach -t dev",
    }))
else:
    print(json.dumps({"permission": "allow"}))
PY
exit 0
