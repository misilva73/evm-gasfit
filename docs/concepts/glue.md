# Glue adjustment

When a runtime measurement targets, say, `ADD`, the fixture's recorded
wall-clock isn't pure `ADD` execution — it also contains the cost of the
**glue opcodes** that set up arguments, advance the program counter, and
clean the stack. NNLS attributes the entire `intercept + slope·opcount`
budget to the target, so if `PUSH` and `JUMPDEST` runtimes happen to scale
with the loop body, the inferred `target_coef` is contaminated.

Glue adjustment subtracts a per-fixture estimate of that overhead from each
fitted target coefficient **before** it is converted to gas.

Enable it under [`glue_adjustment`](../reference/config.md#glue_adjustment):

```yaml
glue_adjustment:
  enabled: true
```

## The 30 canonical glue opcodes

`evm-gasfit` does **not** dynamically discover glue opcodes — the list is
hardcoded in [`src/evm_gasfit/glue/required.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/glue/required.py)
under `PRICED_GLUE_SPECS`. Each opcode lives in one of four tiers; the tier
controls how its per-client runtime is estimated.

| Tier | Opcodes | How its runtime is fit |
| --- | --- | --- |
| `pure` (5) | `ISZERO`, `JUMPDEST`, `POP`, `STOP`, `SWAP` | Single-feature NNLS per `(client, opcode)`. `POP` / `STOP` have no driver fixture yet, so they're skipped with a warning. `SWAP` is fit jointly over `SWAP1..SWAP16`. |
| `cycle` (8) | `CALLDATASIZE`, `DUP`, `GAS`, `MLOAD`, `PUSH`, `PUSH0`, `STATICCALL` | One joint NNLS per client over all cycle opcodes simultaneously, with family members (`DUP1..16`, `PUSH1..32`) row-wise summed into a single canonical feature. |
| `mixed_a` (16) | `ADD`, `AND`, `CALLDATACOPY`, `CALLDATALOAD`, `DIV`, `EXP`, `GT`, `JUMPI`, `LT`, `MSTORE`, `MSTORE8`, `MUL`, `PC`, `RETURNDATASIZE`, `SELFBALANCE`, `SUB` | Single-feature NNLS per `(client, opcode)`, but the LHS (`test_runtime_ms`) is **pre-adjusted** by subtracting contributions from priced `pure` + `cycle` partners. |
| `mixed_b` (2) | `JUMP`, `KECCAK256` | Same as `mixed_a` but partners may also come from priced `mixed_a` results. |

The tier order is a static dependency graph: `pure` and `cycle` produce
runtimes that `mixed_a` consumes, which in turn produce runtimes that
`mixed_b` consumes. No cycles, no iteration to convergence.

(Some target opcodes — like `ADD` — are **both** a target you'd want to price
and a glue contaminant on other targets. That's why they appear in
`mixed_a` / `mixed_b`: they need a fit of their own first.)

## Step 1 — detect per-test glue partners

For every `(spec, model_by-combo)` slice, the detector
([`glue/detect.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/glue/detect.py))
walks every canonical glue opcode and asks: *did this opcode's per-fixture
count co-vary with the target opcount?* It computes:

- The **Pearson correlation** between the glue opcode's per-fixture count
  and `opcount`.
- The **mean delta ratio** \(\text{ratio} = \overline{\Delta\text{glue count}} / \overline{\Delta\text{opcount}}\)
  taken over the sorted-by-opcount sequence.

An opcode is kept as a partner only when both:

\[
\text{corr} \ge 1 - \texttt{ratio corr eps}
\quad \text{and} \quad
\text{ratio} \ge 5 \times 10^{-4} .
\]

`ratio_corr_eps` defaults to `0.05` (see
[`glue_adjustment`](../reference/config.md#glue_adjustment)); the ratio floor
is a fixed `_RATIO_FLOOR` constant. The output is `glue_opcodes_by_test.csv`
— one row per `(test_name, target_opcode, model_by-combo, glue_opcode)`
holding the `corr` and `ratio` of each surviving partner.

Family opcodes (`DUP1..DUP16`, `PUSH1..PUSH32`, `SWAP1..SWAP16`) are folded
into their canonical name before the thresholding so a fixture's `DUP`
contribution is the sum of all 16 family members.

## Step 2 — estimate per-client glue runtimes

For each priced glue opcode, the estimator
([`glue/estimate.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/glue/estimate.py))
runs the appropriate tiered fit and writes one row per `(client, glue_opcode)`
to `glue_results.csv` with `glue_runtime_ms`, `p_value`, `rsquared`, and
confidence interval columns. The `mixed_*` tiers pre-adjust their LHS using
already-priced partners from earlier tiers — same subtraction shape as the
target-coefficient adjustment below.

## Step 3 — adjust the target coefficient

For every row of `results.csv`, the adjuster
([`glue/adjust.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/glue/adjust.py))
looks up each partner from step 1, multiplies its `ratio` by that client's
fitted glue runtime, and sums:

\[
\Delta_\text{glue}
= \sum_g \texttt{ratio}_g \cdot \texttt{glue runtime ms}_g
\]

\[
\text{adjusted target coef ms}
= \max\!\big(0,\ \text{target coef ms} - \Delta_\text{glue} \big)
\]

The lower and upper CI bounds are shifted by the same \(\Delta_\text{glue}\)
and clipped at zero identically. Only the target coefficient is adjusted —
the intercept and `model_params` extras are left alone.

### Per-glue-fit quality gates

A glue opcode contributes to \(\Delta_\text{glue}\) **only if** its per-client
fit passes both:

| Knob | Default |
| --- | --- |
| `glue_contribution_p_value_threshold` | `0.05` |
| `glue_contribution_rsquared_threshold` | `0.5` |

A partner whose fit failed, was skipped (`POP`/`STOP`), or has NaN
statistics is dropped from the sum. Every such skip is recorded; the
proposal report's `Missing glue adjustments` warnings section enumerates the
affected `(client, gas_param)` pairs so reviewers know which proposed values
don't carry a glue correction.

## What lands on disk

When `glue_adjustment.enabled: true`:

- `glue_results.csv` — per `(client, glue_opcode)` fit results.
- `glue_opcodes_by_test.csv` — per-test partner ratios from step 1.
- `glue_opcodes_autogenerated_report.md` — per-opcode summary with plots.
- The proposal layer consumes `adjusted_target_coef_*` columns wherever a
  matching row exists; absent rows fall back to the original `target_coef_*`.
