# NNLS modeling

`evm-gasfit` fits one **non-negative least squares** (NNLS) regression per
`(spec, model_by-combo, client)`. NNLS is the engine that turns measured EVM
runtimes into runtime coefficients; the proposal layer then converts those
into gas costs.

## The regression equation

For each `model_by` slice of a `ModelSpec`, the fit solves:

\[
\text{test runtime ms}
= \text{intercept}
+ \text{target coef} \cdot \text{opcount}
+ \sum_i \text{param}_i \cdot \text{opcount} \cdot \text{param value}_i .
\]

- `test_runtime_ms` is the per-fixture wall-clock runtime in milliseconds.
- `opcount` is the count of the **target opcode** in that fixture.
- Each `param_i` is one of the spec's `model_params` entries other than
  `target_coef`. Its column in the design matrix is the **interaction**
  `opcount × param_value` — these features carry per-opcode marginal costs of
  parameters like memory size or copy length.
- The intercept absorbs fixed per-fixture overhead and is **also** constrained
  to be non-negative — a column of ones is prepended to the design matrix
  before `scipy.optimize.nnls` is called
  ([`modeling/nnls.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/modeling/nnls.py)).

### Why non-negativity?

Gas costs cannot be negative. Ordinary OLS often resolves noise by handing
two correlated features opposite signs that cancel out; NNLS forbids that and
either drives such a coefficient to exactly zero or distributes the signal
across the remaining features. The trade-off is that a coefficient sitting at
the boundary (zero) is an active constraint, not a fitted value — see the
p-value treatment below.

### Features that get dropped

A `model_params` entry is silently dropped when its `param_value` is
**constant** across the filtered fixtures: the interaction column would be a
scalar multiple of `opcount`, indistinguishable from `target_coef`. The fit
proceeds with the remaining features and emits a `dropping extra feature`
warning ([`modeling/estimate.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/modeling/estimate.py)).

### Fits that get skipped

A `(slice, client)` combination is skipped with a warning — and contributes no
row to `results.csv` — when any of:

- Fewer observations than features plus the intercept plus one.
- `opcount` is constant or all-zero (would make `target_coef` unidentifiable).
- The NNLS solver itself raises.

If **every** `(spec, slice, client)` is skipped, the pipeline raises
`ModelingError`.

## Bootstrap inference

Standard errors, confidence intervals, and p-values come from a
**non-parametric bootstrap** over the rows of the design matrix
([`modeling/results.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/modeling/results.py)).
Defaults: `bootstrap_iterations: 1000`, `random_seed: 42` — both configurable
under [`modeling`](../reference/config.md#modeling). The resample indices for
all iterations are drawn up front so the seed remains deterministic even if
some bootstrap fits fail mid-loop.

A bootstrap iteration that raises leaves a row of NaNs in the coefficient
matrix; those rows are filtered out before any statistic is computed (failed
draws are **not** treated as boundary hits at zero).

### P-values

For each coefficient:

\[
p = \max\!\Big( \tfrac{1}{n_\text{success}},\ \frac{1}{n_\text{success}} \sum_{b=1}^{n_\text{success}} \mathbf{1}[\hat\beta_b \le \epsilon] \Big)
\]

with `ε = 1e-12`. The `1/n_success` floor honestly reports "below the
bootstrap resolution" rather than literally zero. A coefficient that the
point estimate already pins at exactly zero gets `p = 1.0` (the constraint is
active; there's no evidence it's nonzero).

### Confidence intervals

`conf_int(alpha=0.05)` returns the empirical 2.5 / 97.5 percentiles of the
successful bootstrap matrix per coefficient — i.e. a **percentile bootstrap
CI**, not a normal-approximation interval.

## What lands in `results.csv`

One row per successful fit, with at minimum:

- `test_name`, `client_name`, `target_opcode`, and one column per `model_by`
  dimension.
- `intercept_runtime_ms`, `intercept_pvalue`.
- `target_coef_runtime_ms`, `target_coef_pvalue`,
  `target_coef_conf_int_low`, `target_coef_conf_int_high`.
- For each surviving extra feature: `<extra>_runtime_ms`, `<extra>_pvalue`,
  `<extra>_conf_int_low`, `<extra>_conf_int_high`.
- `rsquared`, `rsquared_adj`, `nobs`.

The `target_coef_*` columns are what the proposal layer routes to the
`target_coef` entry of `model_params`; the per-extra columns route to the
other `model_params` entries. See [Deriving gas params](gas-params.md) for
the conversion step.

## Quality gates

After all fits land, the per-client worst-case selector flags any candidate
row (`poor_fit = True`) whose p-value or R² crosses one of:

| Knob | Default |
| --- | --- |
| `modeling.poor_fit_p_value_threshold` | `0.05` |
| `modeling.poor_fit_rsquared_threshold` | `0.5` |

Poor-fit selections still make it into `new_gas.csv` (they're the best
candidate available for that client) but surface in the proposal report's
`Poor-fit selections` section so reviewers can decide whether to accept,
broaden the fixture set, or split the spec.
