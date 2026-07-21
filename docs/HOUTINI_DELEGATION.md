# Houtini-LM delegation adapter

The template installs the latest `pcb-ai-adapter` branch declared in
`config/toolchain-policy.json`. It is project-local under `tools/houtini-lm` and
is not copied into Git history.

The fork adds the PCB workflow's token controls:

- bounded path-based file reads with an explicit allowed-root policy;
- compact response metadata and hard visible-output limits;
- automatic DeepSeek thinking selection by task type;
- safe artifact writes restricted to existing project directories;
- model metadata caching and Codex-oriented regression tests;
- one `delegate` tool instead of exposing broad mutation capabilities.

Use it only for cheap-to-verify text work. Electrical, physical and release
decisions stay in Codex and deterministic project scripts.

## Updating from upstream

Do not automatically track the upstream default branch. In the Houtini fork:

1. fetch `https://github.com/houtini-ai/houtini-lm.git` as `upstream`;
2. merge or cherry-pick selected protocol, dependency and bug fixes on a test
   branch;
3. run `npm ci`, `npm test` and any live API smoke test intentionally;
4. push the reviewed adapter commit;
5. merge the reviewed change into `pcb-ai-adapter`;
6. rerun `python scripts/init_project.py` and
   `python scripts/check_pcb_environment.py --deep` in a disposable
   project before adopting it elsewhere.

Existing PCB projects update when their initializer is run again.
