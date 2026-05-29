# Config (YAML)

`evm-gasfit` is driven by a single YAML config validated against the
[`Config`](../api.md) Pydantic model on load. Every field below is either a
top-level key in the YAML or a nested section. Defaults come straight from the
schema in [`src/evm_gasfit/config.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/config.py);
when this page disagrees with that file, the file wins.

A minimal valid config:

```yaml
version: 1
anchor_rate: 1.0e8
clients: [geth, besu]
gas_costs:
  fork: osaka
models:
  presets:
    - arithmetic_add
```

---

## `version`

| | |
| --- | --- |
| **Type** | literal `1` |
| **Required** | yes |

Schema version. Reserved for future migrations; only `1` is accepted today.

## `anchor_rate`

| | |
| --- | --- |
| **Type** | `float` |
| **Required** | yes |

Conversion rate from runtime to gas, in **gas per second**. Every fitted
runtime coefficient (in milliseconds) becomes a gas value via

\[
\text{gas} = \left\lceil \frac{\text{coef}_\text{ms} \cdot \text{anchor rate}}{1000} \right\rceil .
\]

The choice of anchor rate fixes the absolute price scale; see
[Deriving gas params](../concepts/gas-params.md).

## `clients`

| | |
| --- | --- |
| **Type** | `list[str]` |
| **Required** | yes (non-empty, unique) |

The set of client names to model. Rows in the runtimes CSV whose
`client_name` is **not** in this list are dropped. A configured client that
ends up with no successful fits surfaces in the proposal report's
`Incomplete client coverage` warnings section.

## `gas_costs`

| Subfield | Type | Default | Meaning |
| --- | --- | --- | --- |
| `fork` | `str` | — (required) | Fork name (e.g. `osaka`). Resolves to a `GasCosts` table — from the `ethereum-execution-specs` package if the `[specs]` extra is installed, otherwise from the bundled fallback. |
| `overrides` | `dict[str, int]` | `{}` | Per-field patches applied on top of the fork table. Keys **must** be names that exist on the resolved fork. |

!!! warning "Strict override-key validation"
    `overrides` keys are checked against the resolved fork's `field_names`.
    An unknown key fails the config with a "did you mean" hint. To introduce a
    name the fork doesn't already define, declare it in [`new_params`](#new_params)
    instead.

## `glue_adjustment`

Controls the four-tier glue adjustment described in
[Glue adjustment](../concepts/glue.md). All fields are optional.

| Subfield | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | `false` | When `true`, fit glue opcodes and subtract their per-fixture runtime from each target coefficient before converting to gas. |
| `glue_contribution_p_value_threshold` | `float` | `0.05` | A glue opcode's per-client fit must have `p_value < threshold` to contribute to the adjustment. |
| `glue_contribution_rsquared_threshold` | `float` | `0.5` | A glue opcode's per-client fit must also have `R² >= threshold` to contribute. Skipped contributions surface under `Missing glue adjustments` in the proposal. |
| `ratio_corr_eps` | `float` | `0.05` | Detector tolerance: a candidate glue opcode is kept only when its per-fixture count correlates with the target opcount at `corr >= 1 - ratio_corr_eps`. |

## `modeling`

NNLS regression knobs. All fields are optional.

| Subfield | Type | Default | Meaning |
| --- | --- | --- | --- |
| `bootstrap_iterations` | `int` | `1000` | Number of bootstrap resamples used to derive standard errors, confidence intervals, and p-values. |
| `poor_fit_p_value_threshold` | `float` | `0.05` | A winning per-client target_coef whose p-value crosses this threshold is flagged in the report's `Poor-fit selections` section but still counts as the proposal. |
| `poor_fit_rsquared_threshold` | `float` | `0.5` | Same as above but for R². |
| `random_seed` | `int` | `42` | Seed for the bootstrap RNG. |

## `output`

| Subfield | Type | Default | Meaning |
| --- | --- | --- | --- |
| `plots` | `bool` | `true` | When `true`, write per-spec regression figures under `figs/` and render the proposal's `log2(proposed/current)` heatmap. When `false`, the heatmap falls back to a markdown table and `figs/` is skipped. |

## `derived`

| | |
| --- | --- |
| **Type** | `dict[str, str \| {formula: str}]` |
| **Required** | no (defaults to `{}`) |

Post-aggregation gas params computed from other proposed values. Each entry is
either an **alias** (string RHS — copies the value of an existing param) or a
**formula** (mapping with a `formula:` key — a small arithmetic expression).
Formulas support `+ - * / //` and unary `+/-`; identifiers must resolve to a
raw fork field, a `model_params` RHS, a `new_params` key, or an earlier
`derived` entry (declaration order matters).

```yaml
derived:
  ACCESS_LIST_ADDRESS: COLD_ACCOUNT_CODE_ACCESS          # alias
  COLD_ACCOUNT_ACCESS_AVG:
    formula: "(COLD_ACCOUNT_NOCODE_ACCESS + COLD_ACCOUNT_CODE_ACCESS) // 2"
```

See [`src/evm_gasfit/proposal/derived.py`](https://github.com/misilva73/evm-gasfit/blob/main/src/evm_gasfit/proposal/derived.py)
for the exact AST grammar (booleans, function calls, attribute access etc.
are rejected at config load).

## `new_params`

| | |
| --- | --- |
| **Type** | `dict[str, int \| null]` |
| **Required** | no (defaults to `{}`) |

Up-front declaration of gas-param names introduced by the user (via
`model_params` RHS values or `derived` keys) that don't exist on the fork.

| Value | Effect on the proposal diff |
| --- | --- |
| `null` | No prior default to compare against; the `current_gas` column renders blank. |
| `<int>` | Renders as the `current_gas` baseline (useful when proposing a value alongside an existing EIP target). |

!!! warning "Strict declaration rule"
    Every `model_params` RHS that isn't already a fork field **must** appear
    in `new_params`. This catches typos at config load. Conversely, a
    declared `new_params` key that no `model_params` RHS or `derived` formula
    references is a hard error (dead-declaration check).

## `models`

| Subfield | Type | Default | Meaning |
| --- | --- | --- | --- |
| `presets` | `list[str]` | `[]` | Names of bundled `ModelSpec` presets to include. See the [preset catalog](presets.md). |
| `custom` | `list[ModelSpec]` | `[]` | Inline `ModelSpec` definitions. See [Writing custom ModelSpecs](../guides/custom-modelspecs.md) for the field-level reference. |

At least one of `presets` or `custom` must produce a non-empty resolved list,
or the config is rejected.
