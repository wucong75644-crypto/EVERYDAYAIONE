#!/bin/bash
# Synced from Claude PreToolUse: remind review before git push
input=$(cat)
HOOK_INPUT="$input" python3 - <<'PY'
import json, os

raw = os.environ.get("HOOK_INPUT", "")
try:
    d = json.loads(raw)
except Exception:
    d = {}
cmd = d.get("command") or d.get("tool_input", {}).get("command") or ""

if "git push" in cmd:
    print(json.dumps({
        "permission": "allow",
        "agent_message": "[Hook] Review changes before push... Continuing with push",
    }))
else:
    print(json.dumps({"permission": "allow"}))
PY
exit 0
