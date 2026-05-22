# evm-gasfit

Estimate worst-case EVM gas costs from runtime measurements.

`evm-gasfit` is a standalone, analysis-only Python package. Given a YAML test
config, a CSV of per-client runtime measurements, and a JSON of opcode counts,
it fits NNLS regressions, applies an optional glue-opcode adjustment, and
produces a gas-cost proposal as CSV and Markdown artifacts.

## Install

```bash
pip install evm-gasfit
```

Optional extras:

```bash
pip install "evm-gasfit[specs]"   # pulls per-fork GasCosts from ethereum/execution-specs
```

## Quickstart

```bash
evm-gasfit run \
    --config tests.yaml \
    --runtimes runtime.csv \
    --opcounts opcounts.json \
    --out ./out
```

Or from Python:

```python
from evm_gasfit import GasFit

est = GasFit.from_config("tests.yaml")
est.load_runtimes("runtime.csv")
est.load_opcounts("opcounts.json")
est.run()
est.write_reports("./out")
```

See the [API reference](api.md) for the full public surface.
