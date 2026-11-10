# <Mod name>: test notebook

The in-game smoke tests of <mod name>: what each one checks and its last result. Copy this file to
`docs/modding/current_mod/<slug>_tests.md`. The scenarios live in
`docs/modding/current_mod/<slug>_smoke.gc` (from `docs/modding/templates/smoke_test.template.gc`),
one numbered scenario per test; the `smoke-tests` skill explains how to run them.

## Run a test

```bash
python scripts/modding/smoke_test.py install <game> docs/modding/current_mod/<slug>_smoke.gc --scenario <n>
task compile-check
python scripts/modding/smoke_test.py run <game> <test-id>
python scripts/modding/smoke_test.py remove <game>
```

## Tests

| ID | Checks | Scenario | Boot | Pass when | Last result |
|---|---|---|---|---|---|
| T1 | The mod's switch turns the feature on | 1 | debug | `SMOKE PASS feature on` | 2026-01-01 PASS: `SMOKE PASS feature on`, `smoke-start.png` |

Add a row for each new feature; update **Last result** after each run, a failure included.
