# evm-gasfit

Estimate worst-case EVM gas costs from runtime measurements.

`evm-gasfit` is a standalone, analysis-only Python package. Given a YAML test
config, a CSV of per-client runtime measurements, and a JSON of per-fixture
opcode counts, it fits NNLS regressions over the runtimes, applies an optional
glue-opcode adjustment, and produces a gas-cost proposal as CSV and Markdown
artifacts.

```text
runtimes.csv  ─┐
                ├──► fixture parsing ──► NNLS fits ──► glue adjustment ──► gas proposal
opcounts.json ─┤                       (per spec ×                       (per gas param,
                │                       client ×                          worst-case
config.yaml  ──┘                        model_by-combo)                   across clients)
```

## Where to go next

- **Get running.** [Install & quickstart](guides/quickstart.md) covers
  install paths, a minimum-viable config, the CLI, and the Python API.
- **Configure it.** [Config (YAML)](reference/config.md) is the
  section-by-section reference for every YAML field.
- **Extend it.** [Writing custom ModelSpecs](guides/custom-modelspecs.md)
  walks through every field on a `ModelSpec`, with worked examples from the
  preset catalog. The [preset catalog](reference/presets.md) lists every
  bundled recipe.
- **Understand it.** The concept pages explain the mechanics:
    - [NNLS modeling](concepts/nnls.md) — the design matrix, non-negativity,
      bootstrap inference, and quality gates.
    - [Glue adjustment](concepts/glue.md) — the four-tier adjustment that
      strips glue-opcode overhead from each target coefficient.
    - [Deriving gas params](concepts/gas-params.md) — how coefficients become
      gas values, per-client and across-client worst-case selection, and
      derived-formula evaluation.
- **Read the results.** [Reading the outputs](guides/outputs.md) is a
  file-by-file tour of what `write_reports()` emits.
- **Programmatic API.** The full public surface is at
  [API reference](api.md).
