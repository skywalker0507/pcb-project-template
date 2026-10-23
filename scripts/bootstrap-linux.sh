#!/usr/bin/env bash
# Restore the Linux toolchain without changing PCB sources or stage pointers.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export ATO_MP_START_METHOD="${ATO_MP_START_METHOD:-spawn}"

for tool in uv git cargo; do
    command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool. See docs/LINUX.md." >&2; exit 1; }
done
uv python install 3.14
uv venv --allow-existing --python 3.14 .venv-pcb
uv pip install --python .venv-pcb/bin/python -r requirements-pcb-tools.txt pillow
uv venv --allow-existing --python 3.14 .venv-atopile
uv pip install --python .venv-atopile/bin/python 'atopile==0.15.8'

if ! command -v kicad-cli >/dev/null && [[ ! -x tools/kicad-linux/bin/kicad-cli ]]; then
    .venv-pcb/bin/python scripts/install_linux_kicad.py
fi

readarray -t krt < <(.venv-pcb/bin/python - <<'PY'
import json
from pathlib import Path
p=json.loads(Path('config/toolchain-policy.json').read_text())
c=json.loads(Path('config/kicad-routing-tools.json').read_text())
print(p['kicad_routing_tools']['repository'])
print(c['tool']['commit'])
PY
)
if [[ ! -d tools/KiCadRoutingTools/.git ]]; then
    git clone "${krt[0]}" tools/KiCadRoutingTools
    git -C tools/KiCadRoutingTools checkout --detach "${krt[1]}"
elif [[ "$(git -C tools/KiCadRoutingTools rev-parse HEAD)" != "${krt[1]}" ]]; then
    echo "Existing KRT checkout differs from the reviewed commit; inspect it before changing refs." >&2
    exit 1
fi
if [[ -n "$(git -C tools/KiCadRoutingTools status --porcelain --untracked-files=no)" ]]; then
    echo "KRT has tracked changes; inspect them before building a toolchain receipt." >&2
    exit 1
fi
uv venv --allow-existing --python 3.14 .venv-routingtools
uv pip install --python .venv-routingtools/bin/python -r tools/KiCadRoutingTools/requirements.txt
.venv-routingtools/bin/python tools/KiCadRoutingTools/build_router.py --from-source
.venv-pcb/bin/python scripts/record_toolchain_receipt.py --without-konnect
.venv-pcb/bin/python scripts/pcbctl.py verify template
.venv-pcb/bin/python scripts/check_pcb_environment.py --deep
echo "Linux tools are ready. Start with .venv-pcb/bin/python scripts/pcbctl.py status."
