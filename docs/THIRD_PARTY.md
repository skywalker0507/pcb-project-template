# Third-party components and licensing

This repository's own original template code and documentation are offered under the root [MIT License](../LICENSE), **subject to verifying ownership before publication**. This does not relicense upstream projects, binaries, hardware symbols, vendor documentation or optional tools.

| Component | How this project uses it | License action |
| --- | --- | --- |
| [atopile](https://github.com/atopile/atopile) | External electrical design compiler / package ecosystem | Review license for the installed release and any imported components |
| [KiCad](https://www.kicad.org/) | External EDA editor, IPC API, CLI and DRC | Follow its upstream licensing and library attribution rules |
| [KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools) | Optional checked-out router fork | Confirm license for the exact commit; fork status is not relicensing |
| [FreeRouting](https://github.com/freerouting/freerouting) | Optional external router executable | Review the exact release's license before distribution |
| [Konnect](https://github.com/mixelpixx/Konnect) | Optional external KiCad MCP integration | The project documentation identifies AGPL-3.0/commercial terms; verify the version and obligations before enabling or distributing |
| [Houtini-LM](https://github.com/skywalker0507/houtini-lm) | Optional delegated text analysis checkout | Inspect that project's license and dependencies separately |

The template **does not vendor or distribute** those executables in this repository. Initializing a project configures their paths; the operator installs tools separately.

Before making the repository public:
- Verify every committed source file is original or legally redistributable.
- Check copied snippets, schematics, footprints, symbols and images for attribution and license requirements.
- Review upstream licenses at the exact pinned/recorded versions, including network-service obligations where applicable.
- Do not imply that the repository MIT license overrides an external project's terms.
