# tscircuit electrical source

`src/main.tsx` is the primary electrical-design entry point. It intentionally
contains no default circuit because this template has no reviewed product
requirements. Define the board from `config/requirements.json`, then run:

```text
bun install --frozen-lockfile
py scripts/build_electrical.py --backend tscircuit --build default
```

The build writes only `build/generated/tscircuit/<build>/`. Review and import a
candidate deliberately; it never overwrites `elec/layout/default/`.
