#!/usr/bin/env bash
# Dependency preparation only: never deploy, migrate, or start background workers.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
python_bin=${CLOUD_PYTHON:-python3}
"$python_bin" -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,13), "Use Python 3.12"'
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if (!((major===20 && minor>=19) || (major===22 && minor>=12) || major>22)) throw Error("Use Node 22.12+ (recommended) or 20.19+")'
if [[ ! -x .venv/bin/python ]]; then
    "$python_bin" -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3,12), "Existing .venv must use Python 3.12"'
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci --no-audit --no-fund
.venv/bin/python scripts/cloud/dev.py init
printf '%s\n' 'Dependencies ready. See docs/development/CLOUD.md for development services and startup.'
