#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

export PATH="$HOME/.local/bin:$PATH"
export ATO_BINARY="${ATO_BINARY:-$(command -v ato)}"
export ATOPILE_WORKSPACE="$root"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

resolve_node() {
    if command -v node >/dev/null 2>&1; then
        command -v node
        return
    fi

    find "$HOME/.vscode-server/bin" -mindepth 2 -maxdepth 2 \
        -type f -name node -perm -u+x -print 2>/dev/null | head -n 1
}

node_bin="$(resolve_node)"
if [[ -z "$node_bin" ]]; then
    echo "Node.js 18+ not found in PATH or VS Code Server" >&2
    exit 1
fi

if [[ -z "${ATOPILE_PYTHON:-}" ]]; then
    uv_tools="$(uv tool dir)"
    export ATOPILE_PYTHON="$uv_tools/atopile/bin/python"
fi

if [[ ! -x "$ATOPILE_PYTHON" ]]; then
    echo "atopile Python not found: $ATOPILE_PYTHON" >&2
    exit 1
fi

exec "$node_bin" "$root/.codex/mcp/atopile-server.mjs"
