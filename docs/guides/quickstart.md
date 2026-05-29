# Install & quickstart

## Install

From PyPI:

```bash
pip install evm-gasfit
```

For development (editable install plus test tools):

```bash
pip install -e ".[dev]"
```

Optional extras:

```bash
pip install "evm-gasfit[specs]"   # pull per-fork GasCosts from ethereum/execution-specs
pip install "evm-gasfit[docs]"    # mkdocs + Material + mkdocstrings + gen-files (this site)
```

Without the `specs` extra the package falls back to a bundled per-fork table.
Python 3.10 or newer is required.

## A minimal config

`evm-gasfit` is driven by a single YAML config. The smallest valid form:

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

- `clients` is required and acts as a filter: only rows in the runtimes CSV
  whose `client_name` matches an entry survive.
- `models.presets` selects from the [bundled preset catalog](../reference/presets.md).
  Add `models.custom` entries if your test isn't covered — see
  [Writing custom ModelSpecs](custom-modelspecs.md).
- Every gas-param name the model proposes that isn't already a field on the
  fork must be declared in
  [`new_params`](../reference/config.md#new_params). The value is either
  `null` (no prior default to diff against) or an integer baseline that
  renders in the `current_gas` column:

  ```yaml
  new_params:
    COLD_ACCOUNT_NOCODE_ACCESS: null
    STORAGE_WRITE: 2800
  ```

  Undeclared names are a hard config error — this catches typos in
  `model_params` RHS values at load time.

The full field reference lives at [Config (YAML)](../reference/config.md).

## Run it

From the command line:

```bash
evm-gasfit run \
    --config tests.yaml \
    --runtimes runtime.csv \
    --opcounts opcounts.json \
    --out ./out
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Config or input-file error (missing file, validation failure). |
| `2` | Modeling error — every NNLS fit was skipped. |

Or drive it from Python:

```python
from pathlib import Path

from evm_gasfit import GasFit

fit = GasFit.from_config(Path("tests.yaml"))
fit.load_runtimes(Path("runtime.csv"))
fit.load_opcounts(Path("opcounts.json"))
fit.estimate_models()
fit.build_proposal()
fit.write_reports(Path("./out"))
```

If [`glue_adjustment.enabled: true`](../reference/config.md#glue_adjustment),
also call `fit.estimate_glue()` between `estimate_models()` and
`build_proposal()`.

## What lands in `out/`

The headline artifact is `new_gas_proposal.md`. See
[Reading the outputs](outputs.md) for the file-by-file rundown of every CSV
and report section, and [Deriving gas params](../concepts/gas-params.md) for
how the fits in `results.csv` become the proposal in `new_gas.csv`.
