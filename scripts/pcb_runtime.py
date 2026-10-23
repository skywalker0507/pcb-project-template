"""Resolve host tools without changing reviewed PCB policy or design identity.

Project scripts run under Python 3.14. KiCad's extension must instead run under
the interpreter it was built for (the distro Python on Linux).
"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
WINDOWS_KICAD_BIN = Path(r"C:\Program Files\KiCad\10.0\bin")


def venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if WINDOWS else "bin/python")


def kicad_cli(configured: str | Path | None = None) -> Path:
    override = os.environ.get("PCB_KICAD_CLI")
    if override:
        return Path(shutil.which(override) or override).expanduser()
    # Old project configs contain the stock Windows path. Resolve that default
    # on the host; retain explicitly configured non-default paths verbatim.
    if configured and str(configured).replace("\\", "/").lower() != "c:/program files/kicad/10.0/bin/kicad-cli.exe":
        return Path(shutil.which(str(configured)) or configured)
    if WINDOWS:
        return WINDOWS_KICAD_BIN / "kicad-cli.exe"
    portable = ROOT / "tools/kicad-linux/bin/kicad-cli"
    return Path(shutil.which("kicad-cli") or (portable if portable.is_file() else "/usr/bin/kicad-cli"))


def kicad_python() -> Path:
    if value := os.environ.get("PCB_KICAD_PYTHON"):
        return Path(value).expanduser()
    if WINDOWS:
        return WINDOWS_KICAD_BIN / "python.exe"
    if wrapper := shutil.which("pcb-kicad-python"):
        return Path(wrapper)
    portable = ROOT / "tools/kicad-linux/bin/pcb-kicad-python"
    if portable.is_file():
        return portable
    return Path("/usr/bin/python3")


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
        return
    except ImportError:
        pass
    interpreter = kicad_python()
    script = str(Path(sys.argv[0]).resolve())
    if os.environ.get("PCB_KICAD_REEXEC") == script or not interpreter.is_file():
        raise SystemExit(
            f"KiCad pcbnew is unavailable in {interpreter}. Install KiCad's Python "
            "bindings or set PCB_KICAD_PYTHON to their matching interpreter."
        )
    env = os.environ.copy()
    env["PCB_KICAD_REEXEC"] = script
    raise SystemExit(subprocess.call([str(interpreter), script, *sys.argv[1:]], env=env))


def routing_python(root: Path = ROOT, configured: str | None = None) -> Path:
    if value := os.environ.get("PCB_KRT_PYTHON"):
        return Path(value).expanduser()
    if configured and configured.replace("\\", "/") != ".venv-routingtools/Scripts/python.exe":
        path = Path(configured)
        return path if path.is_absolute() else root / path
    return venv_python(root / ".venv-routingtools")


def konnect_binary(root: Path = ROOT) -> Path:
    return root / "tools/konnect" / ("konnect.exe" if WINDOWS else "konnect")


def atopile_binary() -> str:
    local = venv_python(ROOT / ".venv-atopile").with_name("ato.exe" if WINDOWS else "ato")
    return os.environ.get("ATO_BINARY") or (str(local) if local.is_file() else None) or shutil.which("ato") or str(Path.home() / ".local/bin/ato")


def atopile_python() -> Path:
    if value := os.environ.get("ATOPILE_PYTHON"):
        return Path(value).expanduser()
    executable = Path(atopile_binary()).resolve()
    if executable.is_file():
        with executable.open("rb") as handle:
            shebang = handle.readline(4096).decode("utf-8", errors="replace").strip()
        if shebang.startswith("#!/") and "python" in shebang:
            return Path(shlex.split(shebang[2:])[0])
    return Path.home() / ".local/share/uv/tools/atopile/bin/python"


def windows_to_wsl(path: Path) -> str:
    value = PureWindowsPath(str(path))
    if not value.drive or not value.is_absolute():
        raise SystemExit(f"Cannot convert path to WSL form: {path}")
    return f"/mnt/{value.drive[0].lower()}/" + "/".join(value.parts[1:])


def atopile_command(arguments: list[str], distro: str = "Ubuntu-24.04") -> list[str]:
    if WINDOWS:
        command = 'export PATH="$HOME/.local/bin:$PATH"; ' + shlex.join(["ato", *arguments])
        return ["wsl.exe", "-d", distro, "--", "bash", "-lc", command]
    return [atopile_binary(), *arguments]


def atopile_environment() -> dict[str, str]:
    env = os.environ.copy()
    if not WINDOWS:
        # Supported upstream mode; avoids the forkserver's Unix listening
        # socket in cloud environments that only permit approved networking.
        env.setdefault("ATO_MP_START_METHOD", "spawn")
    return env


def atopile_shell(command: str, distro: str, root: Path = ROOT) -> list[str]:
    if WINDOWS:
        return ["wsl.exe", "-d", distro, "--", "bash", "-lc",
                f"cd {shlex.quote(windows_to_wsl(root))} && " + command]
    # Use a non-login shell so a managed environment's PATH/proxy survives.
    return ["bash", "-c", command]
