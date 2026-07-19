#!/bin/bash
# Synced from Claude PreToolUse: block direct package-manager dev servers.
# Only deny when the top-level command is actually starting a dev server.
input=$(cat)
HOOK_INPUT="$input" python3 - <<'PY'
import json, os, re

raw = os.environ.get("HOOK_INPUT", "")
try:
    d = json.loads(raw)
except Exception:
    d = {}
cmd = (d.get("command") or d.get("tool_input", {}).get("command") or "").strip()

# Strip quoted strings so payloads/tests mentioning the phrase cannot false-trigger.
cleaned = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", '""', cmd)
# Ignore nested python/node one-liners.
if re.search(r"\b(python3?|node)\b.*\s-(c|e)\b", cleaned):
    print(json.dumps({"permission": "allow"}))
    raise SystemExit(0)

segments = re.split(r"[;&|\n]", cleaned)
pat = re.compile(
    r"^\s*(?:npm\s+run\s+dev|pnpm(?:\s+run)?\s+dev|yarn\s+dev|bun\s+run\s+dev)(?:\s|$)"
)
deny = False
for seg in segments:
    seg2 = re.sub(r"^(?:\w+=\S+\s+)+", "", seg.strip())
    if pat.match(seg2):
        deny = True
        break

if deny:
    print(json.dumps({
        "permission": "deny",
        "user_message": "已拦截直接启动开发服务器。请用 tmux 运行以便查看日志。",
        "agent_message": "[Hook] BLOCKED: Dev server must run in tmux for log access. Use: tmux new-session -d -s dev then attach.",
    }, ensure_ascii=False))
else:
    print(json.dumps({"permission": "allow"}))
PY
exit 0
