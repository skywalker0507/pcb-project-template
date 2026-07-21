"""Bridge selected tools from atopile's bundled agent registry to JSON."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import traceback

RESULT_PREFIX = "__ATOPILE_MCP_RESULT__"
ALLOWED_TOOLS = {
    "packages_search",
    "packages_install",
    "parts_search",
    "parts_install",
    "parts_datasheet",
    "stdlib_list",
    "stdlib_get_item",
    "workspace_list_targets",
}


async def main() -> dict:
    if len(sys.argv) != 4:
        raise ValueError("Expected: <workspace> <tool-name> <json-arguments>")

    workspace = Path(sys.argv[1]).resolve()
    tool_name = sys.argv[2]
    arguments = json.loads(sys.argv[3])

    if tool_name not in ALLOWED_TOOLS:
        raise ValueError(f"Tool is not allowed: {tool_name}")
    if not workspace.is_dir():
        raise ValueError(f"Workspace does not exist: {workspace}")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")

    os.chdir(workspace)

    from atopile.agent.scope import Scope
    from atopile.agent.tools import create_registry

    scope = Scope(allowed_roots=[workspace])
    registry = create_registry()
    return await registry.execute(tool_name, arguments, scope)


if __name__ == "__main__":
    try:
        payload = {"ok": True, "result": asyncio.run(main())}
    except Exception as error:
        payload = {
            "ok": False,
            "error": str(error),
            "traceback": traceback.format_exc(limit=8),
        }
    print(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, default=str))
