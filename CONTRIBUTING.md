# Contributing

Thanks for helping make AI-assisted PCB development safer and more reproducible.

## Before opening a pull request

1. Open an issue for substantial architectural changes, tool substitutions, or new fabrication vendors.
2. Keep one electrical source of truth (atopile) and one routed-copper source of truth (KiCad). Never let generated candidate routing silently overwrite reviewed copper.
3. Never guess electrical specifications, part pinouts, trace widths, impedance, stackups, DRC exceptions, or manufacturer limits.
4. Do not commit personal MCP configuration, API credentials, BOM pricing under NDA, client hardware, logs, generated builds or manufacturing packages.
5. Include regression tests for changes to rules, CLI argument parsing, security checks, or release guards.

## Local checks (no EDA installation required)

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/validate_requirements.py
python scripts/check_open_source_ready.py
python -m compileall -q scripts tests
```

Toolchain-dependent integration checks require a deliberately installed atopile/KiCad/router environment. See [Quick start](docs/QUICKSTART.md). Clearly distinguish unit-test results from real KiCad DRC and physical board validation.

## Pull request checklist

- Describe what changed and why, with before/after behavior.
- Include exact test commands and results; mark hardware/EDA checks *not run* if unavailable.
- Include relevant policy/manifest hashes for fabrication or routing changes.
- Highlight third-party code copied in, and provide its source and license.
- Never weaken a safety gate just to make a test or a PCB release pass.

Maintainers can ask for smaller PRs or defer features which cannot be reviewed safely.
