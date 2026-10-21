# Experimental tscircuit electrical source

`src/main.tsx` is an optional A/B and research entry point. It is not the
production electrical authority and cannot create an electrical freeze. Run it
independently with:

```text
bun install --frozen-lockfile
py scripts/build_electrical.py --backend tscircuit --build default
```

The build writes only an isolated stage-scoped candidate. Circuit JSON and the
generated footprint library are adapter-private artifacts, not requirements of
the common electrical backend contract and never manufacturing evidence.
