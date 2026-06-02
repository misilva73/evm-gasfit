# Deriving gas params

`evm-gasfit` ends each fit with a runtime coefficient measured in
milliseconds-per-unit; the final report is a table of **gas-per-unit** values
diffed against a fork baseline. This page walks the steps that get from one
to the other.

The pipeline lives under
[`src/evm_gasfit/proposal/`](https://github.com/misilva73/evm-gasfit/tree/main/src/evm_gasfit/proposal):
`aggregate.py` (per-client and across-client selection), `derived.py` (formula
evaluation), and `build.py` (the orchestrator + diff baseline).

## 1 — Coefficient → gas conversion

Every coefficient that comes out of [NNLS](nnls.md) is a slope expressed in
milliseconds-per-unit (where the unit is `opcount` for `target_coef`, and
`opcount × param_value` for extras). It becomes a gas value via the
[`anchor_rate`](../reference/config.md#anchor_rate) declared in the config:

\[
\texttt{new gas decimal} = \frac{\texttt{anchor rate} \cdot \texttt{runtime ms}}{1000}
\]

\[
\texttt{new gas rounded} = \lceil\, \texttt{new gas decimal} \,\rceil
\]

`anchor_rate` is in gas-per-second; runtimes are in milliseconds; the `1000`
is the conversion. The rounded value is what lands in the final proposal;
`new_gas_decimal` is kept alongside it for audit.

## 2 — Coefficient routing into gas-param names

For each row of `results.csv`, the spec's `model_params` dict says which
gas-param name receives which coefficient. The aggregator expands one
results row into **one row per `model_params` entry**:

- The `target_coef` entry receives `target_coef_runtime_ms` — replaced by
  `adjusted_target_coef_runtime_ms` when a [glue adjustment](glue.md) row
  exists for that `(test, target, model_by, client)` tuple. The CI bounds
  follow the same swap. The applied subtraction is recorded in the row's
  `glue_adjustment` column for traceability.
- Each other `model_params` entry receives the corresponding extra
  coefficient (`<coef_name>_runtime_ms`). An extra that was dropped at fit
  time (constant value across fixtures) silently contributes no row.

The result is `new_gas_all_params.csv` — one row per
`(gas_param, client, test_name, target_opcode, model_coef_name, model_by-combo, source_label)`
carrying `runtime_ms`, `pvalue`, `rsquared`, both gas columns, and the
provenance (`test_name`, `target_opcode`, `model_coef_name`, `model_by-combo`,
`source_label`) that the report uses for the `Worst-case provenance` section.
`source_label` names the producing model spec, so two specs that differ only in
`filter_by` stay distinct rows rather than colliding.

## 3 — Per-client worst case

`select_per_client_max` picks one winning row per `(gas_param, client)`:

1. **Qualified pool**: rows with `pvalue < modeling.poor_fit_p_value_threshold`
   **and** `rsquared >= modeling.poor_fit_rsquared_threshold`.
2. If the qualified pool is non-empty, the winner is the row with the
   **largest** `runtime_ms` from that pool.
3. If no row qualifies, fall back to the whole group. Tie-breakers are
   p-value (ascending), then `test_name`, `target_opcode`, `model_coef_name`,
   `model_by-combo` (all ascending) — purely lexical, deterministic across
   runs.

`poor_fit = True` is set on **every** candidate that failed either threshold
(not just the winner), so losing weak fits stay visible; a row that is both
`is_winner` and `poor_fit` is a fallback winner the report surfaces under
`Poor-fit selections`. Every winning row gets `is_winner = True`. Losing
candidates still live in `new_gas_all_params.csv` so the proposal report can
show every per-client contender in its provenance tables.

## 4 — Across-client worst case

`select_across_client_max` reduces the per-client maxima to **one row per
gas param** by picking the client with the largest `runtime_ms`. This is the
worst-case selection — the proposed gas value must cover the slowest client.
The output is `new_gas.csv` with one row per gas param and a
`selected_test` / `selected_opcode` / `selected_model_coef_name` triple
naming the contributing fit. `client_name` on this row is the worst client
(useful for diagnosing whether one client is a persistent outlier).

The proposal report's `Client comparison` section reads
`new_gas_all_params.csv` to show every gas param's worst vs. second-worst
client and their ratio — large ratios flag the worst client as a potential
outlier.

## 5 — Derived params

After steps 3 and 4 run, every `derived:` entry from the config is evaluated
against an environment built from:

- The fork's raw `GasCosts` field values (with `gas_costs.overrides` already
  applied).
- The just-computed `new_gas_rounded` for each fitted gas param.
- Any `new_params` integer baselines.
- Earlier `derived` entries — in **declaration order**, so a later derived
  param can reference an earlier one.

The mini-language ([`proposal/derived.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/proposal/derived.py))
supports `+ - * / //` and unary `+/-` over `int`/`float` constants and
identifiers. **Boolean values, function calls, comparisons, and attribute
access are rejected at config load**, not at evaluation time, so a typo
fails fast.

If any identifier in a formula resolves to `None` (unfit + declared without
a baseline), the result is `None` — the derived param renders as `<no fit>`
in the proposal report rather than crashing.

Two forms are accepted:

```yaml
derived:
  ACCESS_LIST_ADDRESS: COLD_ACCOUNT_CODE_ACCESS          # alias
  COLD_ACCOUNT_ACCESS_AVG:
    formula: "(COLD_ACCOUNT_NOCODE_ACCESS + COLD_ACCOUNT_CODE_ACCESS) // 2"
```

## 6 — Diff baseline and report

`new_gas.csv` is finally diffed against a **patched baseline**:

- Start with `GasCosts(config.gas_costs.fork)`.
- Apply each `config.gas_costs.overrides` entry (already validated at
  config-load against the fork's field names).
- Treat every integer `new_params` value as a baseline for that name;
  `null` `new_params` produce a blank `current_gas` column.

The proposal report renders the diff table, the `Client comparison`
heatmap, and the `Worst-case provenance` collapsibles from this stage. See
[Reading the outputs](../guides/outputs.md) for the file-by-file rundown.
