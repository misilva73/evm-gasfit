# Writing custom ModelSpecs

A `ModelSpec` is one regression recipe: a fixture selector plus a map from
fitted coefficients to gas-param names. The 101 bundled
[presets](../reference/presets.md) are themselves `ModelSpec` instances —
choosing a preset under `models.presets` is exactly the same as pasting its
literal under `models.custom`.

When a behaviour you need isn't covered by a preset, add a `models.custom`
entry. This page documents every field on the spec.

## Minimal custom spec

```yaml
version: 1
anchor_rate: 1.0e8
clients: [geth]
gas_costs:
  fork: osaka
models:
  custom:
    - test_name: test_arithmetic
      target_operation: ADD
      filter_by: ["opcode_ADD-"]
      model_params:
        target_coef: OPCODE_ADD
```

This is identical to the bundled [`arithmetic_add`](../reference/presets.md#arithmetic_add)
preset. Read on for the rest of the fields.

## Field reference

### `test_name` (required)

The EEST test file name that owns this fixture family — for example
`test_arithmetic`, `test_memory_access`, `test_account_access`. Only
fixtures whose parsed `test_name` matches are considered. See the runtimes
CSV / opcounts JSON input contract under the
[implementation plan](https://github.com/misilva73/evm-gasfit/blob/main/.claude/implementation_plan.md)
if you're producing your own fixtures.

### `target_operation` *or* `target_operation_param` (exactly one)

Tells the fit what `target_opcode` means for each row:

- `target_operation: ADD` — literal opcode mnemonic. Every fixture in this
  group must execute `ADD`, and `opcount` must equal the per-fixture `ADD`
  count (the invariant check runs at fit time).
- `target_operation_param: opcode` — pull the target opcode from a
  parsed-param column. Use this when one test file produces fixtures for
  multiple targets and you want to model them jointly. Mutually exclusive
  with `target_operation`.

### `target_operation_count_source` (optional)

Precompile escape hatch — only valid alongside a literal `target_operation`.
A precompile like `SHA256` has no opcount column of its own in the runtimes
data, so its `opcount` must come from the dispatching opcode (typically
`STATICCALL`). Set this to `STATICCALL` (or the relevant dispatcher) when
modelling a precompile.

```yaml
target_operation: SHA256
target_operation_count_source: STATICCALL
```

### `filter_by` (optional, default `[]`)

A list of substrings AND-matched against `fixture_name`. A `!`-prefixed token
**negates** — the fixture name must not contain it.

```yaml
filter_by:
  - "opcode_ADD-"          # require this substring
  - "!benchmark_combined"  # exclude fixtures containing this substring
```

Empty list ⇒ no filtering. Substrings are matched literally (no regex).

### `model_by` (optional, default `[]`)

Group-by axes. The fit is repeated once per unique combination of values in
these columns; each combination produces its own row in `results.csv`.
Useful when the target's runtime depends on a fixture parameter (e.g.
modulus bit-width) that you want to keep as a separate dimension rather
than collapse into a single coefficient.

`model_by` entries can reference either raw parsed params (`mem_size`,
`account_mode`) — internally prefixed as `param_<name>` — or a derived
column declared in `fixture_params`.

### `fixture_params` (optional, default `{}`)

Materializes derived columns on the filtered fixtures. Each key is the
column name you'll use in `model_by` or `model_params`; the value is a
`FixtureParamSpec` carrying:

| Subfield | Type | Meaning |
| --- | --- | --- |
| `source` | `str` | Name of the raw parsed param (without the `param_` prefix). |
| `transform` | `"bytes_to_words"` (optional) | Compute `ceil(source / 32)`, the per-word size used by copy-style opcodes. |
| `values` | `dict[str, float]` (optional) | Remap non-numeric values to floats. Mutually exclusive with `transform`. |

Two patterns from the bundled catalog:

```yaml
# Bytes → 32-byte words (used by MCOPY and the per-word precompiles).
fixture_params:
  copy_words:
    source: copy_size
    transform: bytes_to_words
```

```yaml
# Booleans as 0/1 (used by COLD_ACCOUNT_NOCODE_ACCESS to encode "is a write").
fixture_params:
  update:
    source: value_sent
    values:
      "False": 0
      "True": 1
```

A `transform: bytes_to_words` source must parse as numeric; a `values:`
source can be anything but every observed value must appear as a key
(unmapped values raise `ModelingError`).

### `model_params` (required)

The coefficient → gas-param map. **Must** contain a `target_coef` key:

```yaml
model_params:
  target_coef: OPCODE_MCOPY_BASE     # required
  copy_words: OPCODE_MCOPY_PER_WORD  # one extra per non-target feature
```

Keys other than `target_coef` must resolve to either a `fixture_params` name
or a raw parsed-param column (auto-prefixed `param_<name>` at fit time).
Each becomes a feature `opcount × value` in the design matrix — see
[NNLS modeling](../concepts/nnls.md).

Values (RHS) are the **gas-param names** that the fitted coefficient will
contribute to. Every RHS that isn't already a raw fork field on
`gas_costs.fork` must appear in [`new_params`](../reference/config.md#new_params)
— this catches typos at config load.

## Worked example: a copy-style opcode

`MCOPY` has a base cost and a per-word cost. The bundled preset
[`memory_mcopy`](../reference/presets.md#memory_mcopy) groups by both the
copy size and memory size dimensions and produces both gas params at once:

```yaml
models:
  custom:
    - test_name: test_mcopy
      target_operation: MCOPY
      model_by:
        - copy_size
        - mem_size
      fixture_params:
        copy_words:
          source: copy_size
          transform: bytes_to_words
      model_params:
        target_coef: OPCODE_MCOPY_BASE
        copy_words: OPCODE_MCOPY_PER_WORD
```

`target_coef` carries the base cost (gas per `MCOPY` execution); the
`copy_words` coefficient carries the per-word cost (gas per 32-byte word
copied). The `model_by` dimensions ensure that different `copy_size` /
`mem_size` slices each get their own fit, so a single dominant slice can be
flagged as the worst case downstream.

## Worked example: shared target via `target_operation_param`

When one test file produces fixtures for multiple targets that you want
modelled jointly:

```yaml
models:
  custom:
    - test_name: test_account_access
      target_operation_param: opcode
      filter_by:
        - "CacheStrategy.NO_CACHE"
        - "!AccountMode.EXISTING_CONTRACT"
      model_by:
        - opcode
        - account_mode
      fixture_params:
        update:
          source: value_sent
          values:
            "False": 0
            "True": 1
      model_params:
        target_coef: COLD_ACCOUNT_NOCODE_ACCESS
        update: ACCOUNT_WRITE
```

Here `opcode` is both a `target_operation_param` (each fixture's target is
read from the column) **and** a `model_by` axis (each opcode gets its own
fit). `update` is a `0/1` indicator derived from `value_sent`, and the
fitted `update_runtime_ms` coefficient maps to `ACCOUNT_WRITE`.

## After the fit

Every fitted coefficient becomes a gas value via the `anchor_rate`
conversion; see [Deriving gas params](../concepts/gas-params.md) for the
per-client and across-client selection that follows. If your spec
introduces new gas-param names, remember to declare them in
[`new_params`](../reference/config.md#new_params) — `null` if there's no
prior default to diff against, or an integer baseline.
