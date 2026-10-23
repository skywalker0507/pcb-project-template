# Linux and cloud development

The command-line workflow supports Linux as well as Windows/WSL. Use
`.venv-pcb/bin/python` in place of `py -3.14` on Linux. The same electrical
authority, immutable freeze, routing guards and manufacturing gates apply.

## Install

Ubuntu 24.04 is the tested host. Install Git, curl, GnuPG, build-essential,
system Python, uv, and Rust/cargo before running the project bootstrap.
Rust 1.85.1 was used for the pinned KRT source build.

Install KiCad 10 using the official Ubuntu instructions:
<https://www.kicad.org/download/details/ubuntu/>.

```bash
sudo add-apt-repository --yes ppa:kicad/kicad-10.0-releases
sudo apt update
sudo apt install --no-install-recommends kicad kicad-footprints kicad-symbols python3-pil
bash scripts/bootstrap-linux.sh
```

The bootstrap installs project Python 3.14, atopile 0.15.8 (within the range in
`ato.yaml`), Gerbonara 1.6.3, and the KRT commit selected by the reviewed project
config. KRT's native module is compiled from that checkout, not downloaded from
an unrelated latest release. An existing different or modified KRT checkout is
left untouched. The bootstrap does not initialize a new project, reset the
stage, rewrite reviewed sources, or regenerate a PCB baseline.

If the host can read authenticated APT metadata but cannot install system
packages, configure the official PPA and refresh its package lists, then use:

```bash
python3 scripts/install_linux_kicad.py
bash scripts/bootstrap-linux.sh
```

The portable installer resolves packages without taking a dpkg write lock,
checks sizes and SHA-256 against APT's metadata, and extracts into the ignored
`tools/kicad-linux/` prefix. Its launchers bind libraries, schema resources,
symbols, footprints and the matching system Python to that prefix. It requires
an Ubuntu system Python matching the KiCad package's bindings; a Python 3.14
venv cannot import an extension compiled for the distro's Python 3.12.
Package downloads and compiler caches remain local and can be regenerated.

## Start and verify

```bash
.venv-pcb/bin/python scripts/session_boot.py
.venv-pcb/bin/python scripts/check_pcb_environment.py --deep
.venv-pcb/bin/python scripts/pcbctl.py verify template
.venv-pcb/bin/python scripts/pcbctl.py build --plan
.venv-pcb/bin/python scripts/pcbctl.py build
```

The deep environment check is read-only with respect to board generation. It
checks the real compiler's source validation, KiCad version and Python bindings,
and the pinned KRT native module/CLI. It does not mean a board build, electrical
ERC, manufacturing gate, or release has passed. MCP and the optional Konnect GUI
bridge are not required for deterministic CLI work. The bootstrap explicitly
records Konnect as disabled; an enabled interactive bridge retains its checks.

Linux calls atopile natively and retains managed network proxies. It defaults
to upstream's supported `ATO_MP_START_METHOD=spawn` to avoid a forkserver Unix
listening socket in restricted cloud environments. Explicit user overrides of
that setting are preserved. Windows retains the WSL compiler and bundled KiCad
Python. Atopile compilation runs in its own Python environment, independently
of the project scripts' Python environment.

Optional local overrides:

| Variable | Purpose |
| --- | --- |
| `PCB_KICAD_CLI` | Explicit KiCad CLI executable |
| `PCB_KICAD_PYTHON` | Python/launcher with matching `pcbnew` bindings and Pillow |
| `PCB_KRT_PYTHON` | KRT virtual-environment interpreter |
| `ATO_BINARY` | Native atopile CLI |
| `ATOPILE_PYTHON` | atopile compiler interpreter |

Invalid explicit overrides fail rather than silently selecting another tool.
For optional MCP configuration of an existing board, preserve its state:

```bash
.venv-pcb/bin/python scripts/init_project.py \
  --project-name esp32-s3-remote-flashing-probe --preserve-existing --no-konnect
```

## External inputs still required

The reviewed source uses part-picking constraints. Atopile 0.15.8 requires an
authenticated account for that step. When signed out, the isolated build stops
with `atopile_auth_required` before moving the protected board. Authenticate in
the selected runtime yourself, then retry the build:

```bash
ATO_MP_START_METHOD=spawn .venv-atopile/bin/ato auth login
```

Do not commit credentials. Compiler-only source validation does not need login
and is not a substitute for a successful electrical build.

The existing release handoff references historical files under ignored
`build/` directories. A fresh clone cannot adopt those missing artifacts.
Recover the original immutable freeze/checkpoint and evidence or perform a
reviewed electrical rebuild. Installing Linux tools does not recreate or
approve the missing handoff. Do not reset the stage or substitute an arbitrary
tracked board merely to make status green.

## Verified in ChatGPT Work on 2026-10-23

Environment: Ubuntu 24.04, Python 3.14.8 for scripts, KiCad 10.0.7 with its
Python 3.12 bindings, atopile 0.15.8, Gerbonara 1.6.3, and KRT commit
`dc3f2f01d1793c3e925ab33855f9f891743fe064` built with Rust 1.85.1.

- Actual project `.ato` source passed parse/import/type-graph/instantiation.
- KRT's native import, selected commit and required CLI flags passed.
- A separate synthetic two-pad board routed successfully: unconnected items
  fell from 1 to 0, with zero new KiCad DRC violations.
- KiCad exported Gerbers for that synthetic fixture.
- The configured project PCB was read and checked by KiCad DRC. It still had
  140 unconnected items and 77 release-debt findings; this was a tool-execution
  check, not approval of the PCB.
- Front/back copper images and top/bottom 3D images rendered without a desktop
  session. Optional vendor 3D model completeness was not assessed.
- The actual electrical build reached the sign-in preflight and correctly
  stopped with `atopile_auth_required`.

All smoke outputs stayed in `build/00-shared/tmp/linux-smoke/` or other ignored
artifact lanes. No production board, reviewed electrical source, manufacturing
rule, stage pointer or release baseline was modified by the migration.
