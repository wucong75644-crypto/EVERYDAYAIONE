#!/bin/bash
# Synced from Claude PostToolUse Bash: surface PR URL after gh pr create
input=$(cat)
HOOK_INPUT="$input" python3 - <<'PY'
import json, os, re

raw = os.environ.get("HOOK_INPUT", "")
try:
    d = json.loads(raw)
except Exception:
    d = {}

cmd = d.get("command") or d.get("tool_input", {}).get("command") or ""
out = (
    d.get("output")
    or (d.get("tool_output") or {}).get("output")
    or d.get("stdout")
    or ""
)

if "gh pr create" not in cmd:
    raise SystemExit(0)

m = re.search(r"https://github.com/[^/]+/[^/]+/pull/\d+", out)
if not m:
    raise SystemExit(0)

url = m.group(0)
mm = re.match(r"https://github.com/([^/]+/[^/]+)/pull/(\d+)", url)
repo = mm.group(1) if mm else ""
pr = mm.group(2) if mm else ""
msg = f"[Hook] PR created: {url}\n[Hook] To review: gh pr review {pr} --repo {repo}"
print(json.dumps({"additional_context": msg}, ensure_ascii=False))
PY
exit 0
