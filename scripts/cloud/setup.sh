#!/usr/bin/env bash
# Dependencies by default; --with-services additionally prepares private native
# development services and an audited empty-only bootstrap. Never production.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
if [[ ${1:-} != '' && ${1:-} != --with-services ]]; then
    echo 'Usage: setup.sh [--with-services]' >&2
    exit 2
fi
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
if [[ ${1:-} == --with-services ]]; then
    .venv/bin/python scripts/cloud/native.py
    .venv/bin/python scripts/cloud/services.py prepare "$root"
fi
printf '%s\n' 'Dependencies ready. See docs/development/CLOUD.md for development services and startup.'
