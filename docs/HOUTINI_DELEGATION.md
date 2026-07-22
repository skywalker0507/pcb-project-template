# Houtini-LM delegation adapter

The template expects the manually installed `pcb-ai-adapter` branch declared
in `config/toolchain-policy.json`. It is project-local under
`tools/houtini-lm` and is not copied into Git history.

The fork adds the PCB workflow's token controls:

- bounded path-based file reads with an explicit allowed-root policy;
- compact response metadata and hard visible-output limits;
- automatic DeepSeek thinking selection by task type;
- safe artifact writes restricted to existing project directories;
- model metadata caching and Codex-oriented regression tests;
- one `delegate` tool instead of exposing broad mutation capabilities.

Use it only for cheap-to-verify text work. Electrical, physical and release
decisions stay in Codex and deterministic project scripts.

## Default, low-cost path

The template defaults to `deepseek-v4-flash`, disabled thinking, and a 1600
visible-token ceiling. The active configuration, MCP fragment, and project
initializer use the same values. Use a stronger model only through an explicit,
reviewed per-call override; do not raise the default ceiling for routine logs.

`scripts/create_houtini_job.py` is the deterministic eligibility gate. It never
calls an LLM: it emits a job only for allowed text artifacts totalling at least
16 KiB, and rejects boards, schematics, PDFs, binaries, paths outside this
repository, symlinks, and short receipts. Pipeline and failed KRT candidate
runs invoke it automatically and place a small job under `build/delegated/`.

When a job says `eligible: true`, make exactly one `delegate` call using its
`delegate` object, without first reading its source paths into Codex. The task
is limited to classification and a bounded JSON draft. If no job is emitted,
read the deterministic compact receipt instead. Do not delegate routine JSON,
electrical decisions, placement/routing decisions, DRC waivers, or release.

## Updating from upstream

Do not automatically track the upstream default branch. In the Houtini fork:

1. fetch `https://github.com/houtini-ai/houtini-lm.git` as `upstream`;
2. merge or cherry-pick selected protocol, dependency and bug fixes on a test
   branch;
3. run `npm ci`, `npm test` and any live API smoke test intentionally;
4. push the reviewed adapter commit;
5. merge the reviewed change into `pcb-ai-adapter`;
6. run `python scripts/record_toolchain_receipt.py` and
   `python scripts/check_pcb_environment.py --deep` in a disposable
   project before adopting it elsewhere.

Existing PCB projects update only when their tools are manually updated and a
new receipt is recorded.
