# Security policy

This project combines automation, third-party CLI tools, AI agents and manufacturing outputs. Treat downloaded binaries, MCP servers, source repositories and AI-generated edits as untrusted until reviewed.

## Reporting vulnerabilities

Please use GitHub's **Report a vulnerability** / private security advisory flow if it is enabled for this repository. If the private reporting option is unavailable, request a private contact channel from the maintainer **without posting exploit details or credentials in a public issue**.

No support or response-time SLA is promised.

## Scope and trust boundaries

- Local MCP tools can read project files and may execute subprocesses; review permissions and commands before enabling.
- `scripts/bootstrap-wsl.sh` installs `uv` if missing and installs/upgrades atopile. Inspect remote installer scripts before execution.
- Routing candidates and external tool outputs are never fabrication approval. Review actual KiCad DRC, electrical constraints, safety interlocks and fabrication data independently.
- Do not submit API keys, customer schematics, PCB files under NDA, confidential BOM data, or device credentials to LLM providers without permission.
- Keep `.codex/config.toml`, `build/`, private handoff state, credentials and generated manufacturing outputs outside source control.
- Third-party dependencies retain their own licenses. See [Third-party dependencies](docs/THIRD_PARTY.md).

## Disclosure

Remove compromised credentials from active environments and rotate them immediately. Deleting a secret from the latest commit is **not** sufficient: previously committed secrets may remain in Git history.
