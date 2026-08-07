# tscircuit electrical source

`src/main.tsx` is the primary electrical-design entry point. Initially it
exports a deliberately non-manufacturing, one-resistor smoke-test fixture.
Replace it only after `config/requirements.json` describes a reviewed product,
then run:

```text
bun install --frozen-lockfile
py scripts/build_electrical.py --backend tscircuit --build default
```

The build writes only `build/generated/tscircuit/<build>/`. Review and import a
candidate deliberately; it never overwrites `elec/layout/default/` and is not
manufacturing evidence. Inspect its circuit and footprint-library artifacts
before any physical KiCad work.
