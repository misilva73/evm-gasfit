# evm-gasfit

Estimate worst-case EVM gas costs from runtime measurements. Standalone Python
package; analysis-only (no data ingestion).

## Status

Greenfield. The package skeleton exists ([src/evm_gasfit/__init__.py](src/evm_gasfit/__init__.py))
but **no modules are implemented yet**. The e2e test suite under [tests/](tests/)
is the executable spec — it imports the public API (`from evm_gasfit import GasFit`,
the `evm-gasfit run` CLI, etc.) and drives implementation by failing until each
piece lands.

## Source of truth

[.claude/implementation_plan.md](.claude/implementation_plan.md) is the design
document. Read it before adding code. It specifies, in order:

- §2 input formats (YAML config, runtimes CSV, opcounts JSON, gas-cost defaults)
- §3 pipeline architecture
- §4 modeling (fixture parser, NNLS, glue adjustment, derived params)
- §5 output artifacts (CSVs, markdown reports, figs)
- §6 package layout (the canonical module tree)
- §7 dependencies
- §8 CLI contract (exit codes 0/1/2)

If you find yourself wanting to deviate from the plan, surface that **before**
writing code — either the plan is wrong (update it) or the change isn't justified.
Don't silently expand scope.

## Reference implementation

This package is a port of [misilva73/evm-gas-repricings](https://github.com/misilva73/evm-gas-repricings).
The plan links to specific modules in that repo (e.g. `src/nnls.py`, `src/glue.py`,
`src/data.py`) as the behavioral reference. Several features are **intentionally
dropped** in the port — notably the adaptive low-diff refit (plan §4.2) and
dynamic glue-opcode detection (§4.4: only 12 hardcoded glue opcodes). Don't
re-add dropped features without checking the plan's rationale.

## Working conventions

- **Less is more.** Prefer simple, short, readable code over clever or
  over-engineered solutions. Optimize for the next reader.
- **Don't reinvent the wheel.** If a widely used, well-maintained library
  already does X, depend on it instead of porting or reimplementing X. Reach
  for the dep before writing the helper.
- **Stack defaults.** `pandas` for data manipulation; `seaborn` (built on
  matplotlib) is the preferred plotting front-end — drop down to raw
  matplotlib only for things seaborn can't express.
- **No references to Claude or the plan in shipped artifacts.** Code, tests,
  docstrings, comments, commit messages, and the package's user-facing docs
  (README, generated reports) must not mention Claude, "AI agents", the
  `.claude/` directory, or `.claude/implementation_plan.md`. The plan is an
  internal design document — treat it as scaffolding, not a citation target.
  When you'd otherwise write "per the plan §X", inline the rule itself or
  point at the relevant module/test instead.
- **Test-first.** The e2e tests synthesize their own inputs via
  [tests/_data_synth.py](tests/_data_synth.py) (a known linear model + fixture
  builders). When implementing a module, run the relevant e2e test, watch it
  fail, make it pass. Don't add unit tests speculatively — let the e2e suite
  drive coverage until the public surface stabilizes.
- **Public API is small.** `GasFit`, `GasCosts`, `load_config` re-exported from
  the top-level `evm_gasfit` package. The CLI is `evm-gasfit run …` (see plan §8).
  Everything else is internal.
- **Pydantic v2** for config schemas. **NNLS via `scipy.optimize.nnls`** —
  single backend, no abstraction layer.
- **Python ≥ 3.10**, ruff for lint/format. Prefer `from __future__ import
  annotations` and PEP 604 unions (`X | None`).
- **`pathlib.Path` for all filesystem paths.** No `os.path`, no string
  concatenation, no raw `"/"` literals. Use `Path` for joining (`p / "x.csv"`),
  reading/writing (`.read_text()`, `.write_text()`), and metadata
  (`.exists()`, `.parent`, `.stem`). Public API functions accepting paths
  should type them as `Path` (callers convert at the boundary); convert to
  `str` only when handing off to a library that requires it.
- **Google-style docstrings on the public API.** `GasFit`, `GasCosts`,
  `load_config`, and the CLI entry point are rendered into an auto-deployed
  docs site by mkdocstrings (see plan §11). Type hints carry the types; use the
  docstring body for behavioral notes only. Omit docstrings that would just
  restate the signature.

## Commands

```bash
# install in editable mode with dev extras
pip install -e ".[dev]"

# run the e2e suite
pytest

# run a single test file
pytest tests/test_e2e_cli.py -v

# install the optional ethereum-execution dependency (for live fork gas costs)
pip install -e ".[specs]"

# docs: install + live-reload preview + strict build (matches CI)
pip install -e ".[docs]"
mkdocs serve
mkdocs build --strict
```

## Layout (target — see plan §6)

```
src/evm_gasfit/
├── __init__.py           # public re-exports
├── config.py             # pydantic schema + load_config()
├── defaults/             # per-fork GasCosts (execution-specs + fallback)
├── io/                   # runtimes.py, opcounts.py, fixtures.py (parser)
├── modeling/             # nnls.py, results.py, estimate.py
├── glue/                 # detect.py, estimate.py, adjust.py, required.py
├── proposal/             # aggregate.py, derived.py, build.py
├── reports/              # runtime.py, glue.py, proposal.py, plots.py
├── api.py                # GasFit entry point
└── cli.py                # `evm-gasfit run …`

mkdocs.yml                # docs site config (see plan §11)
docs/
├── index.md              # landing page
└── api.md                # mkdocstrings-rendered public API reference
.github/workflows/
└── docs.yml              # auto-deploys docs to gh-pages on push to main
```

## Notes for AI agents

- Before editing a module, re-read the relevant plan section — the plan is the
  contract, not the existing code (which barely exists).
- The e2e tests pin exact output filenames (`results.csv`, `new_gas.csv`,
  `new_gas_proposal.md`, etc.) and CLI exit codes. Don't rename or restructure
  outputs without updating the plan **and** the tests.
- Fixture names follow the EEST convention
  `<test_file>.py__<test_name>[key_value-key_value-...]` — the parser in
  `io/fixtures.py` is the only place that should know this format.
- Runtimes are **milliseconds**; `anchor_rate` is **gas/second**. Conversion
  lives in the proposal aggregator (§4.5).
- Don't introduce mocks for scipy/pandas/numpy — the tests run the real stack
  end-to-end against synthesized but realistic inputs.
