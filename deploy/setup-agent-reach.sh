#!/usr/bin/env bash
# Prepare a dedicated runtime at the explicitly supplied directory.
set -euo pipefail
if [[ $# != 1 ]]; then
  echo 'Usage: deploy/setup-agent-reach.sh /absolute/path/to/new/runtime' >&2
  exit 2
fi
runtime_dir=$1
if [[ "$runtime_dir" != /* || -e "$runtime_dir" ]]; then
  echo 'Choose an absolute path that does not already exist.' >&2
  exit 2
fi
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${AGENT_REACH_PYTHON_BIN:-python3.11}
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo 'A Python 3.11+ interpreter is required; set AGENT_REACH_PYTHON_BIN explicitly.' >&2
  exit 2
fi
"$python_bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' || {
  echo 'Agent Reach runtime requires Python 3.11 or newer.' >&2
  exit 2
}
command -v git >/dev/null 2>&1 || { echo 'Git is required to install pinned upstream revisions.' >&2; exit 2; }
umask 077
"$python_bin" -m venv "$runtime_dir"
"$runtime_dir/bin/python" -m pip install -r "$repo_dir/backend/requirements-agent-reach.txt" -c "$repo_dir/backend/requirements-agent-reach.freeze.txt"
node_bin=${AGENT_REACH_NODE_BIN:-$(command -v node || true)}
if [[ -n "$node_bin" && -x "$node_bin" ]]; then
  ln -s "$node_bin" "$runtime_dir/bin/node"
else
  echo 'Node is unavailable: video transcript channel must remain disabled.' >&2
fi
# Keep an exact inventory for this deployment. Upstream revisions are pinned;
# transitive versions are recorded here and must be preserved for rollback.
"$runtime_dir/bin/python" -m pip freeze > "$runtime_dir/installed-requirements.txt"
for command_name in agent-reach twitter bili rdt yt-dlp; do
  test -x "$runtime_dir/bin/$command_name"
done
printf 'Runtime prepared. Set AGENT_REACH_BIN_DIR=%s/bin before enabling configured channels.\n' "$runtime_dir"
