# evm-gasfit

[![PyPI version](https://img.shields.io/pypi/v/evm-gasfit.svg)](https://pypi.org/project/evm-gasfit/)
[![Python versions](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://pypi.org/project/evm-gasfit/)
[![CI](https://github.com/misilva73/evm-gasfit/actions/workflows/ci.yml/badge.svg)](https://github.com/misilva73/evm-gasfit/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://misilva73.github.io/evm-gasfit/)
[![License: CC0-1.0](https://img.shields.io/badge/license-CC0--1.0-lightgrey.svg)](LICENSE)

Estimate worst-case EVM gas costs from runtime measurements.

`evm-gasfit` is a standalone, analysis-only Python package. Given a YAML test
config, a CSV of per-client runtime measurements, and a JSON of opcode counts,
it fits NNLS regressions over the runtimes, applies an optional glue-opcode
adjustment, and produces a gas-cost proposal as CSV and Markdown artifacts.

## Install

For development (editable + test tools):

```bash
pip install -e ".[dev]"
```

The optional `specs` extra pulls per-fork `GasCosts` tables directly from
[`ethereum/execution-specs`](https://github.com/ethereum/execution-specs).
Without it, the package falls back to a bundled per-fork table:

```bash
pip install -e ".[specs]"
```

Python 3.10 or newer is required.

## Quickstart

A minimal `tests.yaml`:

```yaml
version: 1
anchor_rate: 1.0e8
clients:
  - geth
  - besu
gas_costs:
  fork: osaka
models:
  presets:
    - arithmetic_add
```

`clients` is required: only rows whose `client_name` matches an entry here are
kept from the runtimes CSV, and a configured client that produced no fits at
all surfaces in the proposal report's `Incomplete client coverage` section.

Any gas-param name that the model proposes but the fork's `GasCosts` doesn't
already define must be declared up front under `new_params`. The value is
either `null` ("no prior default") or an integer that renders as the
`current_gas` baseline in the proposal diff:

```yaml
new_params:
  COLD_ACCOUNT_NOCODE_ACCESS: null   # no prior default to diff against
  STORAGE_WRITE: 2800                # render 2800 in the diff column
```

Names without a declaration are a hard config error — this catches typos in
`model_params` RHS values at load time.

Run the full pipeline from the command line:

```bash
evm-gasfit run \
    --config tests.yaml \
    --runtimes runtime.csv \
    --opcounts opcounts.json \
    --out ./out
```

Exit codes: `0` on success, `1` for config/input errors, `2` for modeling
failures.

Or drive it from Python:

```python
from evm_gasfit import GasFit

fit = GasFit.from_config("tests.yaml")
fit.load_runtimes("runtime.csv")
fit.load_opcounts("opcounts.json")
fit.estimate_models()
fit.build_proposal()
fit.write_reports("./out")
```

## Adapters

### EEST `blockchain_tests` Adapter

Prepare EEST `blockchain_tests` fixtures before joining them to benchmark
runtimes:

```bash
evm-gasfit prepare-eest \
    --eest-fixtures /path/to/fixtures_geth/blockchain_tests \
    --out ./prepared/eest
```

This writes `opcounts.json`, `fixtures.csv`, and `excluded.csv`.
`prepare-eest` reads `_info.metadata.opcode_count` and
`_info.metadata.target_opcode`, derives `block_limit_million` from
`benchmark-gas-value_60M` fixture names, and records stable EEST provenance
(`original_test_name`, `source_path`, `block_index`).
Precompile targets such as `SHA2-256` get a synthetic target count from
`STATICCALL` when EEST traces only the call opcode. 

### `zkevm-metrics` Adapter

Turn a zkevm-benchmark-workload metrics tree into a full set of gasfit inputs:

```bash
evm-gasfit prepare-zkevm \
    --zkevm-metrics /path/to/benchmark/zkevm-metrics \
    --out ./prepared/zkevm
```

This writes `opcounts.json` and `runtimes.csv` for the pipeline, plus
`fixtures.csv` and `excluded.csv` for auditing. `prepare-zkevm` reads
`metadata.opcode_count`, `metadata.target_opcode`, and
`metadata.original_test_name` to build the opcounts, and takes
`test_runtime_ms` from `proving.success.proving_time_ms`.

Fixture names follow the same `<test_file>.py__<test_name>[...]` convention as
`prepare-eest`, deriving `block_limit_million` from `benchmark-gas-value_60M`
test names, so both adapters' outputs join. Precompile targets such as
`SHA2-256` get a synthetic target count from `STATICCALL`, matching
`prepare-eest`.

`client_name` is the record's directory path below `--zkevm-metrics`, joined
with `-` (so a `<client>/<zkvm>` layout yields `<client>-<zkvm>`), falling back
to the root directory name for records sitting directly in it. A `hardware.json`
beside the records is skipped.

Point `--zkevm-metrics` at a directory holding several `<client>/<zkvm>`
subtrees to compare them in one run.

Records that cannot be used are listed in `excluded.csv` with a `reason`
column, covering unreadable or malformed records, missing or unusable
`target_opcode` and `opcode_count`, and proving that crashed, reported no
time, or returned output that did not match the fixture. Because one benchmark
keeps a single fixture name across clients, a record is also dropped when it
repeats a fixture already recorded for its client. When two records for one
fixture report a different target opcode or opcode counts there is no basis for
preferring either, so every record for that fixture is dropped.

## Public API

The top-level package re-exports a small surface:

```python
from evm_gasfit import GasFit, GasCosts, load_config
```

Everything else is internal. See the rendered API reference at
[`docs/api.md`](docs/api.md) (auto-deployed to GitHub Pages from `mkdocs.yml`).

## Outputs

`write_reports(out_dir)` emits:

- `results.csv` — one row per fit (`(spec, model_by-combo, client)`).
- `new_gas_all_params.csv` — every per-client candidate fit, with the
  per-client worst-case pick flagged `is_winner`.
- `new_gas.csv` — worst-case across clients (one row per gas param).
- `runtime_estimation_autogenerated_report.md` — per-spec regression summary.
- `new_gas_proposal.md` — final proposal, opening with a `Contents` TOC.
  Carries a diff table for fitted rows (against patched fork values +
  `new_params` integer baselines), a `Client comparison` section showing
  each parameter's worst vs. second-worst client and a `worst / second-worst`
  ratio (large ratios flag the worst client as an outlier) plus a per-client
  overview of proposed values — rendered as a `log2(proposed / current)`
  heatmap when plots are on (red = more expensive than current,
  green = cheaper, blank rows for `new_params` declared without a baseline)
  or as a markdown table when plots are off, a `Worst-case provenance` section
  with one collapsible block per gas param showing every per-client candidate
  (one row per `(test_name, target_opcode, model_coef_name, model_by)` combo,
  one column per client, cells = proposed gas); the cell the per-client
  selector picked as that client's worst-case is highlighted (outlined on the
  heatmap, bolded in the markdown-table fallback), and a warnings section
  containing `Missing parameters`
  (proposed names that produced no value), `Incomplete client coverage`
  (proposed names fit for some clients but missing on others),
  `Missing glue adjustments`, and `Other`. A trailing `Poor-fit selections`
  section flags winning fits whose p-value or R² crossed the
  `modeling.poor_fit_*_threshold` knobs (`Winners with poor fit`) plus any
  losing candidates that failed the same thresholds (`Other weak candidates`).

When glue adjustment is enabled, `glue_results.csv`,
`glue_opcodes_by_test.csv`, and `glue_opcodes_autogenerated_report.md` are
written too. Each priced glue opcode's per-client contribution is applied
only when its fit passes both `glue_contribution_p_value_threshold` (default
`0.05`) and `glue_contribution_rsquared_threshold` (default `0.7`); skipped
contributions surface under `Missing glue adjustments` in the proposal so
the affected gas params and clients are auditable. When `output.plots: true`,
regression and diagnostic figures land under `figs/`.

## Tests

```bash
pytest
```

The end-to-end suite synthesizes its own inputs and exercises the public API
and CLI; there are no fixtures to download.

## License

See [`LICENSE`](LICENSE).
