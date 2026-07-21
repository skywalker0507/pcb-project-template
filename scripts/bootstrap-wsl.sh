#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if ! grep -qi microsoft /proc/version 2>/dev/null; then
    echo "warning: this is Linux but does not appear to be WSL" >&2
fi

for command_name in git curl; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "missing prerequisite: $command_name" >&2
        exit 1
    fi
done

export PATH="$HOME/.local/bin:$PATH"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

required_version="0.15.7"
installed_version=""
if command -v ato >/dev/null 2>&1; then
    installed_version="$(ato --version 2>/dev/null | tail -n 1 | tr -d '\r')"
fi

if [[ "$installed_version" != "$required_version" ]]; then
    echo "installing atopile $required_version with uv"
    uv tool install --force "atopile==$required_version"
    hash -r
fi

actual_version="$(ato --version | tail -n 1 | tr -d '\r')"
if [[ "$actual_version" != "$required_version" ]]; then
    echo "expected atopile $required_version, got $actual_version" >&2
    exit 1
fi

git config core.autocrlf input
git config core.filemode false

echo "repository: $root"
echo "atopile: $(command -v ato) ($actual_version)"
echo "kernel: $(uname -a)"

echo "bootstrap complete. Run python scripts/check_pcb_environment.py --deep from Windows."
