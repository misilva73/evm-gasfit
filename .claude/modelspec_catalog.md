# Default ModelSpec preset catalog — proposal

Expands `src/evm_gasfit/defaults/models.py::PRESETS` from 3 entries to cover
every fixture in `2026-05-22T00-04-40Z_2026-05-24T10-51-12Z/runtimes.csv`
(5,419 unique fixtures, 70 distinct `(test_file, test_name)` pairs, 5 clients).

The 3 existing presets (`arithmetic_add`, `account_access`, `storage_access`)
are preserved and folded into their groups below. `arithmetic_add` gains an
explicit `filter_by: ["opcode_ADD-"]` (rule 3 below) so the substring filter
doesn't also match `opcode_ADDMOD-…` fixtures; the others are verbatim.

## Design rules used

1. **Literal `target_operation`** when each opcode in the test maps to its own
   `OPCODE_<X>` gas param (arithmetic, bitwise, control-flow, etc.). One
   preset per opcode.
2. **`target_operation_param: opcode`** when a single test sweeps many opcodes
   that all map to *one* shared gas param (account access reads, log family).
3. **`filter_by`** is declared explicitly whenever a `fixture_name` substring
   match is needed; there is no auto-default. It is added in three cases:
   (a) the test bundles multiple opcodes (e.g. `test_arithmetic`,
   `test_bitwise`, `test_block_context_ops`) — every preset off that test
   carries an `opcode_<X>` filter to isolate its slice; (b) carving a
   sub-family out of a test (BLS G1 vs G2, cold-storage cache strategies);
   (c) the target opcode's `opcode_<X>` token is a prefix of another opcode's
   token in the same test, so the plain substring filter would leak — fix is
   a trailing-dash anchor (e.g. `opcode_ADD-` to exclude `opcode_ADDMOD-`,
   `opcode_MUL-` to exclude `opcode_MULMOD-`, `opcode_PUSH0-` to exclude
   `opcode_PUSH1…32`, `opcode_MSTORE-` to exclude `opcode_MSTORE8-`). When
   the test exercises a single opcode and isolates its fixtures by
   `test_name` alone, `filter_by` is omitted.
4. **`model_by`** exposes a fixture-param to the fit (one fit per value combo).
   Used for: payload-size sweeps (mem_size, log_size, msg_size, return_size,
   copy_size, calldata_size, size, num_pairs, num_rounds, k, mod_bits), and
   for tests where a SSTORE/MOD variant flag matters
   (write_new_value/existing_slots, mod_bits). Presets keep using these
   logical names; on `fixtures_df` and in the output CSVs they appear under
   the `param_<name>` prefix (`param_mem_size`, `param_mod_bits`, …) that
   the parser uses to avoid colliding with opcode-mnemonic columns. Derived
   names declared via `fixture_params:` (e.g. `calldata_words`, `size_words`)
   stay unprefixed.
5. **One preset per (test, output-gas-param)**: if the same test feeds two
   distinct gas params (e.g. base vs. per-word), it gets two presets that
   share `test_name`+`target_operation` but differ in `model_params`.
   Aggregation in §4.6 routes each `results.csv` row back to its producing
   preset by `source_label`, so two presets sharing `test_name` (even with the
   same target and `model_by`) never claim each other's fits — this is safe.

## Relationship to the priced-glue set

18 of the catalog's targets — `ADD`, `AND`, `CALLDATACOPY`, `CALLDATALOAD`,
`DIV`, `EXP`, `GT`, `JUMPI`, `LT`, `MSTORE`, `MSTORE8`, `MUL`, `PC`,
`RETURNDATASIZE`, `SELFBALANCE`, `SUB` (mixed-A), `JUMP`, `KECCAK256`
(mixed-B) — also appear in the priced-glue table
([src/evm_gasfit/glue/required.py](../src/evm_gasfit/glue/required.py); plan §4.4).
The two pipelines fit the same fixtures independently and produce
**separate** estimates:

- The **modelspec** preset's `target_coef` lands in `results.csv` and is
  the value `new_gas.csv` proposes. The §4.4 post-fit
  `compute_glue_adjustment` subtracts every priced glue partner's
  contribution from `target_coef_runtime_ms` (`glue_adjustment` column on
  `new_gas_all_params.csv`).
- The **glue tier** runs a separate per-`(client, canonical_name)`
  regression with the LHS pre-adjusted by partners from earlier tiers
  (`glue_results.csv`). That coefficient feeds *into* the §4.4
  adjustment for downstream modelspecs but does **not** replace the
  modelspec value in `new_gas.csv`.

Under the detector's `corr ≈ 1` assumption the two coefficients converge,
because the LHS subtraction and the post-fit coefficient subtraction are
algebraically equivalent (plan §4.4). Divergence in practice usually
signals either a `corr < 1` partner (the ratio approximation is loose) or
a partner whose own fit is biased. Both are auditable: `glue_results.csv`
carries the partner's fit; `glue_opcodes_by_test.csv` carries the
detector's `corr`/`ratio` per partner.

## New gas-param names introduced (lenient warning per §2.5)

The osaka fallback does not include these — listing them as `target_coef`
RHS values will emit a warning at config-load time and surface in
`new_gas_proposal.md` warnings, which is fine: they represent params the
runtimes data is offering to *propose*.

- `OPCODE_CALLDATACOPY_PER_WORD` — from `test_calldatacopy_from_origin` (fallback ships a shared `OPCODE_COPY_PER_WORD`; this preset proposes a CALLDATACOPY-specific value)
- `OPCODE_CODECOPY_PER_WORD` — from `test_codecopy_benchmark` (same reasoning)
- `OPCODE_MCOPY_PER_WORD` — from `test_mcopy` (same reasoning)
- `COLD_ACCOUNT_NOCODE_ACCESS` — from `cold_account_nocode_access` (fallback ships a single `COLD_ACCOUNT_ACCESS = 2600`; the upstream EIP-8038 proposal splits cold account access by whether the target carries code)
- `COLD_ACCOUNT_CODE_ACCESS` — from `cold_account_code_access` (same reasoning as `COLD_ACCOUNT_NOCODE_ACCESS`)
- `COLD_ACCOUNT_NOCODE_WRITE` / `COLD_ACCOUNT_CODE_WRITE` — combined cold access+write cost fitted directly from the `value_sent_1` (value-transferring) fixtures of `cold_account_*_write`. These are scaffolding params: the per-write delta `ACCOUNT_WRITE` is recovered from them in `derived`
- `ACCOUNT_WRITE` — **derived**, not fitted: `max(0, COLD_ACCOUNT_CODE_WRITE − COLD_ACCOUNT_CODE_ACCESS, COLD_ACCOUNT_NOCODE_WRITE − COLD_ACCOUNT_NOCODE_ACCESS)`. The combined write is bounded by a single worst-case client per context (rather than summing two independent per-param maxima), and the worst of the two contexts wins
- `STORAGE_WRITE` — **derived**, not fitted: `max(0, COLD_STORAGE_WRITE − COLD_STORAGE_ACCESS)`, where `COLD_STORAGE_WRITE` (a raw osaka field, =5000) is now fitted directly from the `write_new_value_True` fixtures as the combined access+write cost

The underlying `model_by` columns (`calldata_size`, `return_size`,
`code_size`, `copy_size`, `msg_size`) carry **byte** units in the runtime
inputs. To fit a per-word coefficient directly — and keep gas-param names
aligned with the fork's `*_PER_WORD` convention — each of these presets
declares a `fixture_params` entry with `transform: bytes_to_words`
(`x → ceil(x / 32)`) and feeds the derived `*_words` column into
`model_params` instead of the raw byte column. The `÷ 32` lives in the
spec, not in the regression coefficient, and no `derived:` formula is
needed to recover the per-word number.

> **Schema extension required.** `FixtureParamSpec` in
> `src/evm_gasfit/config.py` currently supports only `source` and `values`.
> `transform: Literal["bytes_to_words"] | None` must be added to the schema
> and `_materialize_derived` in `src/evm_gasfit/modeling/estimate.py` must
> grow a branch that applies `np.ceil(x / 32).astype(float)` when
> `transform == "bytes_to_words"`. Without this change, every preset below
> that declares `transform:` will fail Pydantic validation at config-load
> time. Track as a prerequisite landing-step alongside the catalog itself.

## Catalog (proposed)

### Arithmetic — 15 presets (one per opcode, plus a second per mod-family op)

| Preset | `test_name` | `target_operation` | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `arithmetic_add` *(existing, plus explicit `filter_by`)* | `test_arithmetic` | `ADD` (`filter_by: ["opcode_ADD-"]`) | — | `OPCODE_ADD` |
| `arithmetic_sub` | `test_arithmetic` | `SUB` | — | `OPCODE_SUB` |
| `arithmetic_mul` | `test_arithmetic` | `MUL` (`filter_by: ["opcode_MUL-"]`) | — | `OPCODE_MUL` |
| `arithmetic_div` | `test_arithmetic` | `DIV` | — | `OPCODE_DIV` |
| `arithmetic_sdiv` | `test_arithmetic` | `SDIV` | — | `OPCODE_SDIV` |
| `arithmetic_signextend` | `test_arithmetic` | `SIGNEXTEND` | — | `OPCODE_SIGNEXTEND` |
| `arithmetic_exp` | `test_arithmetic` | `EXP` | — | `OPCODE_EXP_BASE` |
| `arithmetic_mod` | `test_arithmetic` | `MOD` | — | `OPCODE_MOD` |
| `arithmetic_mod_bits` | `test_mod` | `MOD` | `[mod_bits]` | `OPCODE_MOD` |
| `arithmetic_smod` | `test_arithmetic` | `SMOD` | — | `OPCODE_SMOD` |
| `arithmetic_smod_bits` | `test_mod` | `SMOD` | `[mod_bits]` | `OPCODE_SMOD` |
| `arithmetic_addmod` | `test_arithmetic` | `ADDMOD` | — | `OPCODE_ADDMOD` |
| `arithmetic_addmod_bits` | `test_mod_arithmetic` | `ADDMOD` | `[mod_bits]` | `OPCODE_ADDMOD` |
| `arithmetic_mulmod` | `test_arithmetic` | `MULMOD` | — | `OPCODE_MULMOD` |
| `arithmetic_mulmod_bits` | `test_mod_arithmetic` | `MULMOD` | `[mod_bits]` | `OPCODE_MULMOD` |

Notes:

- MOD/SMOD/ADDMOD/MULMOD each get **two presets**: a basic one off
  `test_arithmetic` (no `mod_bits` sweep) and a `_bits` variant off the
  dedicated `test_mod` / `test_mod_arithmetic` test (which exposes
  `mod_bits`). Both write the same `OPCODE_<X>` gas param, so the
  aggregator's per-client max across `(test, model_by-combo)` picks the
  worst case across both sources automatically.
- `arithmetic_exp` writes only the base coefficient; the per-byte
  coefficient needs an EXP-byte-sweep test that isn't in this dataset.

### Bitwise — 9 presets

| Preset | `test_name` | `target_operation` | Writes |
| --- | --- | --- | --- |
| `bitwise_and` | `test_bitwise` | `AND` | `OPCODE_AND` |
| `bitwise_or` | `test_bitwise` | `OR` | `OPCODE_OR` |
| `bitwise_xor` | `test_bitwise` | `XOR` | `OPCODE_XOR` |
| `bitwise_byte` | `test_bitwise` | `BYTE` | `OPCODE_BYTE` |
| `bitwise_shl` | `test_bitwise` | `SHL` | `OPCODE_SHL` |
| `bitwise_shr` | `test_bitwise` | `SHR` | `OPCODE_SHR` |
| `bitwise_sar` | `test_bitwise` | `SAR` | `OPCODE_SAR` |
| `bitwise_not` | `test_not_op` | `NOT` | `OPCODE_NOT` |
| `bitwise_clz` | `test_clz_same` | `CLZ` | `OPCODE_CLZ` |

### Comparison — 6 presets

| Preset | `test_name` | `target_operation` | Writes |
| --- | --- | --- | --- |
| `comparison_lt` | `test_comparison` | `LT` | `OPCODE_LT` |
| `comparison_gt` | `test_comparison` | `GT` | `OPCODE_GT` |
| `comparison_slt` | `test_comparison` | `SLT` | `OPCODE_SLT` |
| `comparison_sgt` | `test_comparison` | `SGT` | `OPCODE_SGT` |
| `comparison_eq` | `test_comparison` | `EQ` | `OPCODE_EQ` |
| `comparison_iszero` | `test_iszero` | `ISZERO` | `OPCODE_ISZERO` |

### Stack — 4 presets

| Preset | `test_name` | Target | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `stack_push0` | `test_push` | `PUSH0` (`filter_by: ["opcode_PUSH0-"]`) | — | `OPCODE_PUSH0` |
| `stack_push` | `test_push` | param `opcode` (`filter_by: ["opcode_PUSH"]` then exclude `PUSH0`) | `[opcode]` | `OPCODE_PUSH` |
| `stack_dup` | `test_dup` | param `opcode` | `[opcode]` | `OPCODE_DUP` |
| `stack_swap` | `test_swap` | param `opcode` | `[opcode]` | `OPCODE_SWAP` |

Notes:

- `PUSH0` is split out: `OPCODE_PUSH0` is its own gas param (2 vs PUSH's 3).
  Using `filter_by: ["opcode_PUSH0-"]` (trailing `-` so it doesn't also match
  `PUSH1`…) keeps it isolated, then the `stack_push` preset uses
  `target_operation_param: opcode` over the remaining `PUSH1..PUSH32` and
  maps every fit to the shared `OPCODE_PUSH`. (The aggregator picks the
  worst case across the 32 per-opcode fits.)

### Control flow — 5 presets

| Preset | `test_name` | `target_operation` | Writes |
| --- | --- | --- | --- |
| `control_flow_jump` | `test_jump_benchmark` | `JUMP` | `OPCODE_JUMP` |
| `control_flow_jumpi` | `test_jumpi_fallthrough` | `JUMPI` | `OPCODE_JUMPI` |
| `control_flow_jumpdest` | `test_jumpdests` | `JUMPDEST` | `OPCODE_JUMPDEST` |
| `control_flow_pc` | `test_pc_op` | `PC` | `OPCODE_PC` |
| `control_flow_gas` | `test_gas_op` | `GAS` | `OPCODE_GAS` |

### Block & TX context — 11 presets

| Preset | `test_name` | `target_operation` | Writes |
| --- | --- | --- | --- |
| `block_basefee` | `test_block_context_ops` | `BASEFEE` | `OPCODE_BASEFEE` |
| `block_blobbasefee` | `test_block_context_ops` | `BLOBBASEFEE` | `OPCODE_BLOBBASEFEE` |
| `block_chainid` | `test_block_context_ops` | `CHAINID` | `OPCODE_CHAINID` |
| `block_coinbase` | `test_block_context_ops` | `COINBASE` | `OPCODE_COINBASE` |
| `block_gaslimit` | `test_block_context_ops` | `GASLIMIT` | `OPCODE_GASLIMIT` |
| `block_number` | `test_block_context_ops` | `NUMBER` | `OPCODE_NUMBER` |
| `block_prevrandao` | `test_block_context_ops` | `PREVRANDAO` | `OPCODE_PREVRANDAO` |
| `block_timestamp` | `test_block_context_ops` | `TIMESTAMP` | `OPCODE_TIMESTAMP` |
| `block_blockhash` | `test_blockhash` | `BLOCKHASH` | `OPCODE_BLOCKHASH` (with `model_by: [block]`) |
| `tx_gasprice` | `test_call_frame_context_ops` | `GASPRICE` | `OPCODE_GASPRICE` |
| `tx_origin` | `test_call_frame_context_ops` | `ORIGIN` | `OPCODE_ORIGIN` |

Notes:

- `test_call_frame_context_ops` is *the same test_name in two files*
  (`test_call_context.py` and `test_tx_context.py`). Since `test_name` is the
  join key, one preset per opcode (ADDRESS, CALLER, GASPRICE, ORIGIN) is the
  safe way: each row is unambiguously routed by its `target_opcode`.
- `test_blockhash` fixtures mix two shapes: numeric `block_N` tokens
  (parsed as `params["block"] = "N"`) and the bare tag `random` (which
  parses to nothing — no `block` param on those rows). `model_by: [block]`
  groups the `random` fixtures into a single NaN group via `dropna=False`
  in [estimate.py:292](src/evm_gasfit/modeling/estimate.py#L292); the per-client
  worst-case selection then picks the slower of the two shapes
  automatically.

### Call context — 8 presets

| Preset | `test_name` | Target | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `call_address` | `test_call_frame_context_ops` | `ADDRESS` | — | `OPCODE_ADDRESS` |
| `call_caller` | `test_call_frame_context_ops` | `CALLER` | — | `OPCODE_CALLER` |
| `call_callvalue` | `test_callvalue_from_origin` | `CALLVALUE` | — | `OPCODE_CALLVALUE` |
| `call_calldataload` | `test_calldataload` | `CALLDATALOAD` | `[calldata_size]` | `OPCODE_CALLDATALOAD` |
| `call_calldatasize` | `test_calldatasize` | `CALLDATASIZE` | `[calldata_size]` | `OPCODE_CALLDATASIZE` |
| `call_returndatasize` | `test_returndatasize_nonzero` | `RETURNDATASIZE` | `[returned_size]` | `OPCODE_RETURNDATASIZE` |
| `call_calldatacopy` | `test_calldatacopy_from_origin` | `CALLDATACOPY` | `[calldata_size, mem_size]` | `target_coef: OPCODE_CALLDATACOPY_BASE`, `calldata_words: OPCODE_CALLDATACOPY_PER_WORD` (via `fixture_params.calldata_words = {source: calldata_size, transform: bytes_to_words}`) |
| `call_returndatacopy` | `test_returndatacopy` | `RETURNDATACOPY` | `[return_size, mem_size]` | `target_coef: OPCODE_RETURNDATACOPY_BASE`, `return_words: OPCODE_RETURNDATACOPY_PER_WORD` (via `fixture_params.return_words = {source: return_size, transform: bytes_to_words}`) |

The size dimensions (`calldata_size`, `return_size`) are in **bytes**, so
each copy preset declares a `fixture_params` entry with
`transform: bytes_to_words` (`x → ceil(x / 32)`) and feeds the derived
`*_words` column into `model_params`. The fitted coefficient is then the
per-word number directly, and no `derived:` formula is needed to bridge
the unit gap. The §4.7 rounding rule still applies on the way to
`new_gas.csv`.

### Memory — 5 presets

| Preset | `test_name` | Target | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `memory_mload` | `test_memory_access` | `MLOAD` | `[mem_size]` | `OPCODE_MLOAD_BASE` |
| `memory_mstore` *(anchored `filter_by: [opcode_MSTORE-]`)* | `test_memory_access` | `MSTORE` | `[mem_size]` | `OPCODE_MSTORE_BASE` |
| `memory_mstore8` | `test_memory_access` | `MSTORE8` | `[mem_size]` | `OPCODE_MSTORE8_BASE` |
| `memory_msize` | `test_msize` | `MSIZE` | — | `OPCODE_MSIZE` |
| `memory_mcopy` | `test_mcopy` | `MCOPY` | `[copy_size, mem_size]` | `target_coef: OPCODE_MCOPY_BASE`, `copy_words: OPCODE_MCOPY_PER_WORD` (via `fixture_params.copy_words = {source: copy_size, transform: bytes_to_words}`) |

### Account / storage / state — 10 presets

The 3 existing presets `account_access`, `storage_access`, and the prior
draft entries `account_ext_query_warm` / `storage_sload_warm` /
`storage_sstore` are dropped and replaced with a granular set that mirrors
the per-parameter source mapping used in the upstream EIP-8038 estimation
script (`_STATE_ACCESS_PARAM_SOURCES`). Each gas param draws from one or
more benchmark tests under specific `(cache_strategy, account_mode)`
filters; the §4.6 aggregator's per-client worst-case selection composes
them. Where the upstream script uses an `account_mode != X` exclusion, the
presets below enumerate the *included* values as separate entries.

| Preset | `test_name` | Target | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `warm_storage_access_sload` | `test_sload_same_key_benchmark` | `SLOAD` | — | `WARM_ACCESS` |
| `warm_account_access` | `test_ext_account_query_warm` | param `opcode` | `[opcode]` | `WARM_ACCESS` |
| `cold_storage_sload` | `test_sload_bloated` | `SLOAD` (`filter_by: ["CacheStrategy.NO_CACHE"]`) | `[existing_slots]` | `COLD_STORAGE_ACCESS` |
| `cold_storage_sstore_access` | `test_sstore_bloated` | `SSTORE` (`filter_by: ["CacheStrategy.NO_CACHE", "write_new_value_False"]`) | `[existing_slots]` | `COLD_STORAGE_ACCESS` |
| `cold_storage_sstore_write` | `test_sstore_bloated` | `SSTORE` (`filter_by: ["CacheStrategy.NO_CACHE", "write_new_value_True"]`) | `[existing_slots]` | `COLD_STORAGE_WRITE` (combined access+write) |
| `cold_account_nocode_access` | `test_account_access` | param `opcode` (`filter_by: ["CacheStrategy.NO_CACHE", "!AccountMode.EXISTING_CONTRACT", "value_sent_0", "overhead_baseline_False"]`) | `[opcode, account_mode]` | `COLD_ACCOUNT_NOCODE_ACCESS` |
| `cold_account_nocode_write` | `test_account_access` | param `opcode` (`filter_by: ["CacheStrategy.NO_CACHE", "!AccountMode.EXISTING_CONTRACT", "value_sent_1", "overhead_baseline_False"]`) | `[opcode, account_mode]` | `COLD_ACCOUNT_NOCODE_WRITE` (combined access+write) |
| `cold_account_code_access` | `test_account_access` | param `opcode` (`filter_by: ["CacheStrategy.NO_CACHE", "!AccountMode.EXISTING_EOA", "value_sent_0", "overhead_baseline_False"]`) | `[opcode, account_mode]` | `COLD_ACCOUNT_CODE_ACCESS` |
| `cold_account_code_write` | `test_account_access` | param `opcode` (`filter_by: ["CacheStrategy.NO_CACHE", "!AccountMode.EXISTING_EOA", "value_sent_1", "overhead_baseline_False"]`) | `[opcode, account_mode]` | `COLD_ACCOUNT_CODE_WRITE` (combined access+write) |
| `account_codecopy` | `test_codecopy_benchmark` | `CODECOPY` | `[code_size, mem_size]` | `target_coef: OPCODE_CODECOPY_BASE`, `code_words: OPCODE_CODECOPY_PER_WORD` (via `fixture_params.code_words = {source: code_size, transform: bytes_to_words}`) |
| `account_codesize` | `test_codesize` | `CODESIZE` | — | `OPCODE_CODESIZE` |
| `account_selfbalance` | `test_selfbalance` | `SELFBALANCE` | — | `OPCODE_SELFBALANCE` |

Notes:

- **New gas-param names** introduced by this group
  (`COLD_ACCOUNT_NOCODE_ACCESS`, `COLD_ACCOUNT_CODE_ACCESS`,
  `COLD_ACCOUNT_NOCODE_WRITE`, `COLD_ACCOUNT_CODE_WRITE`) are listed with their
  per-param rationale in the top-level "New gas-param names introduced" section.
  `STORAGE_WRITE` / `ACCOUNT_WRITE` are no longer fitted — they are derived from
  the combined-write params (see below).
- **Write deltas are priced jointly, not independently.** A write to new state
  is *never charged without its cold access*, so the chargeable cost is the
  combined `access + write`. Pricing `access` and `write` as two independent
  per-param worst-cases over-charges: `max` is subadditive, so the worst-access
  client and the worst-write client can differ and their sum exceeds any single
  client's combined cost. Instead each test is split by the
  "touches new state" signal — `write_new_value_{False,True}` for SSTORE,
  `value_sent_{0,1}` for account access — into a **read-only access fit** and a
  **combined access+write fit** (`COLD_STORAGE_WRITE`, `COLD_ACCOUNT_*_WRITE`),
  each a single-coefficient regression selected purely via `filter_by`. The
  write delta is then recovered in `derived`:
  - `STORAGE_WRITE = max(0, COLD_STORAGE_WRITE − COLD_STORAGE_ACCESS)`
  - `ACCOUNT_WRITE = max(0, COLD_ACCOUNT_CODE_WRITE − COLD_ACCOUNT_CODE_ACCESS, COLD_ACCOUNT_NOCODE_WRITE − COLD_ACCOUNT_NOCODE_ACCESS)`

  The combined cost is thus bounded by one worst-case client (tighter than the
  sum of two maxima), the subtraction uses the *global* published access
  worst-case, and the `max(0, …)` floor keeps a degenerate negative delta from
  leaking. This needs `max`/`min` in the derived mini-language (§4 derived
  params). It does **not** apply to per-unit compute params
  (`*_PER_WORD/_ROUND/_POINT`): those scale with input size and are correctly
  priced by independent-max.
- **`existing_slots` model_by on storage presets.** `test_sload_bloated`
  and `test_sstore_bloated` sweep the same opcode under two storage states
  (`existing_slots_{True,False}` — whether the targeted slot is already
  populated). The storage presets group on `existing_slots` so NNLS fits one
  model per state; the §4.6 worst-case selection then propagates the slower of
  the two into `new_gas.csv`.
- **`account_mode` enumeration.** Upstream filters are
  `account_mode != EXISTING_CONTRACT` (NOCODE pool: `NON_EXISTING_ACCOUNT` +
  `EXISTING_EOA`) and `account_mode != EXISTING_EOA` (CODE pool:
  `NON_EXISTING_ACCOUNT` + `EXISTING_CONTRACT`). Both presets mirror upstream:
  NOCODE via `filter_by: ["!AccountMode.EXISTING_CONTRACT"]`, CODE via
  `filter_by: ["!AccountMode.EXISTING_EOA"]`. Each exposes `account_mode` on
  `model_by` so NNLS fits one model per included mode; the §4.6 worst-case
  selection picks the slower. The four account presets all share `test_name` +
  `target_operation_param` + `model_by` and differ only in `filter_by`
  (NOCODE/CODE × read/write) and the gas param they write, so the §4.6
  aggregator routes each `results.csv` row back to its producing preset by
  `source_label` rather than by the key shape — neither preset claims the
  other's fits, and the overlapping `NON_EXISTING_ACCOUNT` mode stays distinct
  candidate rows (one per preset) instead of being deduplicated.
- **`overhead_baseline` exclusion.** `test_account_access` now sweeps an
  `overhead_baseline` variant alongside the account/value dimensions: the
  `True` fixtures run the surrounding harness *without* the target account
  operation, so their runtime measures loop overhead rather than account
  access. All four presets pin `overhead_baseline_False` so the fit only sees
  the real access fixtures — the baseline variants would otherwise enter the
  same `(opcode, account_mode)` group and drag the fitted coefficient down.
  This is a `filter_by` exclusion, not a subtraction: the intercept the
  regression already fits absorbs per-fixture overhead, so the baseline rows
  add nothing the model needs. (Should the baselines later be used as an
  explicit overhead estimate, that's a separate preset writing its own param —
  not a change to these four.)
- **Filter tokens use the EEST enum stringification.** EEST renders
  `cache_strategy` and `account_mode` parameters as
  `cache_strategy_CacheStrategy.<VALUE>` and
  `account_mode_AccountMode.<VALUE>` in the fixture id (the dotted prefix
  is the Python enum class name). `filter_by` here uses the `<Class>.<Value>`
  fragment directly as the substring — it is unique enough to disambiguate
  within the affected tests and avoids hard-coding the redundant
  `cache_strategy_`/`account_mode_` key prefix.
- **Derived params not shipped.** The upstream script also emits
  `GAS_STORAGE_CLEAR_REFUND`, `ACCESS_LIST_STORAGE_KEY_COST`, and
  `ACCESS_LIST_ADDRESS_COST` from formulas over the directly estimated
  numbers. Presets carry `ModelSpec`s only, not derived formulas, so
  these belong in the YAML config's `derived:` section (the §2.5 example
  config already shows the shape).
- **`account_codecopy` / `account_codesize` / `account_selfbalance`** are
  retained from the prior draft. They're not state-access in the
  EIP-8038 sense — they target the executing contract's own code/balance
  — but they fit naturally in this grouping and the upstream reference
  script doesn't touch them either way.

### Storage (test_storage.py) — 2 presets

| Preset | `test_name` | Target | `filter_by` | Writes |
| --- | --- | --- | --- | --- |
| `storage_tload` | `test_tload` | `TLOAD` | — | `OPCODE_TLOAD` |
| `storage_tstore` | `test_tstore` | `TSTORE` | — | `OPCODE_TSTORE` |

### Hashing — 1 preset

| Preset | `test_name` | Target | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `keccak` | `test_keccak_diff_mem_msg_sizes` | `KECCAK256` | `[mem_size]` | `target_coef: OPCODE_KECCAK256_BASE`, `msg_words: OPCODE_KECCAK256_PER_WORD` (via `fixture_params.msg_words = {source: msg_size, transform: bytes_to_words}`) |

`test_log_benchmark` (LOG0…LOG4) is deferred — see "Deferred to future
iteration" below.

### System — 1 preset

| Preset | `test_name` | Target | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `system_create` | `test_create` | param `opcode` | `[opcode]` | `OPCODE_CREATE_BASE` |

### Precompiles — 19 presets

Every precompile preset uses the §2.1 escape hatch: `target_operation` carries
the precompile's display name (which lands on `target_opcode` in
`results.csv` / `new_gas.csv`, keeping each precompile's output rows readable;
the aggregator itself routes rows back to their preset by `source_label`, §4.6),
and `target_operation_count_source: STATICCALL` tells the §2.3 invariant —
and the §4.4 glue candidate filter — that the `opcount` column is actually
backed by the `STATICCALL` column. Each preset declares its own
`filter_by` substring (e.g. `["bls12_g1add"]`) to pick out the precompile's
slice of fixtures within the shared test file, since the display name does
not appear as a fixture token.

| Preset | `test_name` | Target | `model_by` | Writes |
| --- | --- | --- | --- | --- |
| `precompile_ecrecover` | `test_ecrecover` | `ECRECOVER` (count via `STATICCALL`, `filter_by: ["ecrecover"]`) | — | `PRECOMPILE_ECRECOVER` |
| `precompile_sha256_fixed` | `test_sha256_fixed_size` | `SHA256` (count via `STATICCALL`, `filter_by: ["sha256"]`) | — | `target_coef: PRECOMPILE_SHA256_BASE`, `size_words: PRECOMPILE_SHA256_PER_WORD` (via `fixture_params.size_words = {source: size, transform: bytes_to_words}`) |
| `precompile_sha256_uncachable` | `test_sha256_uncachable` | `SHA256` (count via `STATICCALL`, `filter_by: ["sha256"]`) | — | `target_coef: PRECOMPILE_SHA256_BASE`, `size_words: PRECOMPILE_SHA256_PER_WORD` (via `fixture_params.size_words = {source: size, transform: bytes_to_words}`) |
| `precompile_ripemd160_fixed` | `test_ripemd160_fixed_size` | `RIPEMD160` (count via `STATICCALL`, `filter_by: ["ripemd160"]`) | — | `target_coef: PRECOMPILE_RIPEMD160_BASE`, `size_words: PRECOMPILE_RIPEMD160_PER_WORD` (via `fixture_params.size_words = {source: size, transform: bytes_to_words}`) |
| `precompile_ripemd160_uncachable` | `test_ripemd160_uncachable` | `RIPEMD160` (count via `STATICCALL`, `filter_by: ["ripemd160"]`) | — | `target_coef: PRECOMPILE_RIPEMD160_BASE`, `size_words: PRECOMPILE_RIPEMD160_PER_WORD` (via `fixture_params.size_words = {source: size, transform: bytes_to_words}`) |
| `precompile_identity_fixed` | `test_identity_fixed_size` | `IDENTITY` (count via `STATICCALL`, `filter_by: ["identity"]`) | — | `target_coef: PRECOMPILE_IDENTITY_BASE`, `size_words: PRECOMPILE_IDENTITY_PER_WORD` (via `fixture_params.size_words = {source: size, transform: bytes_to_words}`) |
| `precompile_identity_uncachable` | `test_identity_uncachable` | `IDENTITY` (count via `STATICCALL`, `filter_by: ["identity"]`) | — | `target_coef: PRECOMPILE_IDENTITY_BASE`, `size_words: PRECOMPILE_IDENTITY_PER_WORD` (via `fixture_params.size_words = {source: size, transform: bytes_to_words}`) |
| `precompile_blake2f` | `test_blake2f_benchmark` | `BLAKE2F` (count via `STATICCALL`, `filter_by: ["blake2f"]`) | — | `target_coef: PRECOMPILE_BLAKE2F_BASE`, `num_rounds: PRECOMPILE_BLAKE2F_PER_ROUND` |
| `precompile_blake2f_uncachable` | `test_blake2f_uncachable` | `BLAKE2F` (count via `STATICCALL`, `filter_by: ["blake2f"]`) | — | `target_coef: PRECOMPILE_BLAKE2F_BASE`, `num_rounds: PRECOMPILE_BLAKE2F_PER_ROUND` |
| `precompile_p256verify` | `test_p256verify` | `P256VERIFY` (count via `STATICCALL`, `filter_by: ["p256verify"]`) | — | `PRECOMPILE_P256VERIFY` |
| `precompile_p256verify_uncachable` | `test_p256verify_uncachable` | `P256VERIFY` (count via `STATICCALL`, `filter_by: ["p256verify"]`) | — | `PRECOMPILE_P256VERIFY` |
| `precompile_point_evaluation` | `test_point_evaluation` | `POINT_EVALUATION` (count via `STATICCALL`, `filter_by: ["point_evaluation"]`) | — | `PRECOMPILE_POINT_EVALUATION` |
| `precompile_point_evaluation_uncachable` | `test_point_evaluation_uncachable` | `POINT_EVALUATION` (count via `STATICCALL`, `filter_by: ["point_evaluation"]`) | — | `PRECOMPILE_POINT_EVALUATION` |
| `precompile_bn128_add` | `test_alt_bn128` | `ECADD` (count via `STATICCALL`, `filter_by: ["bn128_", "!bn128_mul"]`) | `[bn128]` | `PRECOMPILE_ECADD` |
| `precompile_bn128_mul` | `test_alt_bn128` | `ECMUL` (count via `STATICCALL`, `filter_by: ["bn128_mul_"]`) | — | `PRECOMPILE_ECMUL` |
| `precompile_bn128_add_uncachable` | `test_alt_bn128_uncachable` | `ECADD` (count via `STATICCALL`, `filter_by: ["ec_add"]`) | — | `PRECOMPILE_ECADD` |
| `precompile_bn128_mul_uncachable` | `test_alt_bn128_uncachable` | `ECMUL` (count via `STATICCALL`, `filter_by: ["ec_mul_"]`) | — | `PRECOMPILE_ECMUL` |
| `precompile_bn128_pairing` | `test_alt_bn128_benchmark` | `ECPAIRING` (count via `STATICCALL`, `filter_by: ["num_pairs"]`) | — | `target_coef: PRECOMPILE_ECPAIRING_BASE`, `num_pairs: PRECOMPILE_ECPAIRING_PER_POINT` |
| `precompile_bn128_pairing_alt` | `test_ec_pairing` | `ECPAIRING` (count via `STATICCALL`) | — | `target_coef: PRECOMPILE_ECPAIRING_BASE`, `num_pairs: PRECOMPILE_ECPAIRING_PER_POINT` |

Notes:

- **Per-word coefficients are fit directly.** SHA256, RIPEMD160, IDENTITY,
  KECCAK256, and all `*COPY` per-word coefficients use the same recipe:
  `model_params.target_coef` mapped to the BASE gas-param and a derived
  `*_words` coefficient mapped to the PER_WORD gas-param via
  `fixture_params.<name> = {source: <size column>, transform: bytes_to_words}`.
  The size-sweep column (`size`, `msg_size`, `copy_size`, …) must not appear
  in `model_by` — pooling across it is what gives the per-word feature the
  variation NNLS needs. Other grouping dimensions are fine (e.g. `keccak`
  groups by `mem_size`). This requires the `transform` schema extension noted
  at the top of this document.
- **BLAKE2F filler base.** EIP-152 prices BLAKE2F entirely per-round, so
  `PRECOMPILE_BLAKE2F_BASE` is shipped at `0` in the osaka fallback. The
  catalog still has to declare a `target_coef` slot because the NNLS recipe
  requires one, and the per-round value is recovered from the `num_rounds`
  coefficient. Downstream consumers should ignore the BASE row or treat it
  as informational.
- **Pairing per-point fitted, not grouped.** Earlier drafts grouped by
  `num_pairs`; that prevents the per-point coefficient from being a
  fit output. Switching to `model_by: []` with `num_pairs` as a
  `model_params` extra recovers `PRECOMPILE_ECPAIRING_PER_POINT` from the
  slope directly.
- **BN128 add-family fit per-variant under one preset.** `test_alt_bn128`
  carries four add-family variants (`bn128_add`, `bn128_add_negative`,
  `bn128_add_infinities`, `bn128_double`) and three mul-family variants
  (`bn128_mul_32_byte_coord_and_scalar`,
  `bn128_mul_32_byte_coord_and_2_scalar`,
  `bn128_mul_infinities_32_byte_scalar`). All four add variants are
  invocations of the same `ECADD` precompile (address 0x06) with different
  inputs, so they share a `target_operation: ECADD` and write
  `PRECOMPILE_ECADD`. A single `precompile_bn128_add` preset groups them
  via `model_by: [bn128]` — the EEST variant token is parsed into the
  `bn128` fixture-param (`add` / `add_negative` / `add_infinities` /
  `double`) — so NNLS fits one model per variant and the per-gas-param
  worst-case selection picks the slowest. The `filter_by: ["bn128_",
  "!bn128_mul"]` pair admits the four add fixtures while excluding the
  mul-family that shares the same `test_name`; the trailing `!bn128_mul`
  negation token is the same AND-substring matcher as positive tokens but
  inverted. Mul variants stay in their own `precompile_bn128_mul` preset
  (target `ECMUL`) — they share cost semantics and fit as one combined
  regression.

### BLS12-381 precompiles — 6 presets

`test_bls12_381` and `test_bls12_381_uncachable` use a bare-tag carve-out:
the variant token (`bls12_g1add`, `bls12_g2add`, `bls12_g1msm`,
`bls12_g2msm`, `bls12_fp_to_g1`, `bls12_fp_to_g2`) determines which BLS
op was tested. The parser sees `bls12=g1add` etc.

| Preset | `test_name` | Target | Writes |
| --- | --- | --- | --- |
| `precompile_bls_g1add` | `test_bls12_381` | `BLS12_G1ADD` (count via `STATICCALL`, `filter_by: ["bls12_g1add"]`) | `PRECOMPILE_BLS_G1ADD` |
| `precompile_bls_g2add` | `test_bls12_381` | `BLS12_G2ADD` (count via `STATICCALL`, `filter_by: ["bls12_g2add"]`) | `PRECOMPILE_BLS_G2ADD` |
| `precompile_bls_fp_to_g1` | `test_bls12_381` | `BLS12_MAP_FP_TO_G1` (count via `STATICCALL`, `filter_by: ["bls12_fp_to_g1"]`) | `PRECOMPILE_BLS_G1MAP` |
| `precompile_bls_fp_to_g2` | `test_bls12_381` | `BLS12_MAP_FP_TO_G2` (count via `STATICCALL`, `filter_by: ["bls12_fp_to_g2"]`) | `PRECOMPILE_BLS_G2MAP` |
| `precompile_bls_g1msm` | `test_bls12_g1_msm` | `BLS12_G1MSM` (count via `STATICCALL`, `filter_by: ["bls12_g1msm"]`) | `PRECOMPILE_BLS_G1MUL` (`model_by: [k]`) |
| `precompile_bls_g2msm` | `test_bls12_g2_msm` | `BLS12_G2MSM` (count via `STATICCALL`, `filter_by: ["bls12_g2msm"]`) | `PRECOMPILE_BLS_G2MUL` (`model_by: [k]`) |

All BLS specs use the §2.1 precompile shape: a distinct `target_operation`
display name per variant (used as `target_opcode` in the output rows) plus
`target_operation_count_source: STATICCALL` so the §2.3 invariant and the
§4.4 glue candidate filter both read from the `STATICCALL` opcount column.
The non-target opcodes (GAS, CALLDATACOPY, etc.) get absorbed into the
intercept or get adjusted away by glue. The presets sharing `test_bls12_381`
are routed by `source_label` in the aggregator (§4.6), and their distinct
`target_operation` display names keep the output rows readable, so they need
no further plumbing.

`test_bls12_381_uncachable` shares the same six variant tags as the four
cachable presets plus the two MSM ones — adding eight more presets here
would be redundant since the aggregator merges across `test_name`. Leave
those measurements to the user via `custom:`.

## E2E testing

The catalog lands behind the existing e2e suite under [tests/](../tests/),
which synthesizes its own inputs via [tests/_data_synth.py](../tests/_data_synth.py).
The pipeline-level mechanics this catalog relies on are already pinned:

- **Preset registry behavior** — [tests/test_e2e_presets.py](../tests/test_e2e_presets.py)
  uses `arithmetic_add` as the canonical preset. After fix 1 the preset
  gains `filter_by: ["opcode_ADD-"]`; the synthetic fixtures emitted by
  `make_block_limit_fixtures(target_opcode="ADD", params={"opcode": "ADD"})`
  still match (token boundary is `-`), so this test must keep passing
  unchanged.
- **`fixture_params` materialization** — [tests/test_e2e_fixture_params.py](../tests/test_e2e_fixture_params.py)
  covers the `source` + `values` map shape used by `update`-style derived
  columns on the cold-account / sstore presets.
- **`target_operation_count_source` escape hatch** — [tests/test_e2e_precompile.py](../tests/test_e2e_precompile.py)
  covers the precompile shape (synthetic display name + `STATICCALL`
  opcount column) shared by all 22 precompile + 6 BLS presets.

The catalog adds three new validation needs that the existing suite does
not cover. Each should be a new e2e test added before the catalog lands:

- **Anchor correctness (ADD vs ADDMOD, MUL vs MULMOD, PUSH0 vs PUSH1…32).**
  Synthesize fixtures for *both* the target opcode and its prefix-overlap
  sibling in the same `test_name` slice, run the pipeline with the catalog
  preset, and assert `results.csv` contains rows for the target opcode only
  — no leakage from the sibling. One parameterized test over the three
  pairs is enough.
- **`bytes_to_words` transform (new schema feature).** Synthesize a
  copy-style sweep where `<size>` is a known byte range (e.g.
  `calldata_size in {32, 64, 96, 128}`), build a fixture spec with
  `fixture_params.<name> = {source: calldata_size, transform: bytes_to_words}`,
  and assert the fitted per-word coefficient recovers the true per-word
  slope of the synthetic linear model (`size / 32 → coefficient`). Pairs
  naturally with the schema extension landing-step called out at the top
  of this document.
- **Catalog smoke test.** A single test that loads a fixture YAML listing
  every preset in this catalog under `models.presets`, against a synthetic
  runtimes/opcounts pair covering one fixture per preset, and asserts the
  pipeline runs end-to-end without raising. The goal is to catch typos in
  `test_name` / `target_operation` / `model_params` keys at landing time,
  not to validate fit quality. Skip presets whose `test_name` isn't in the
  synthetic dataset.

Per the project's test-first convention, write each test, watch it fail
against the current `PRESETS` dict, then expand `PRESETS` (and the schema)
until it passes. The 3 existing presets named in the e2e suite
(`arithmetic_add`, `account_access`, `storage_access`) must keep their
test names valid — `arithmetic_add` gains a `filter_by` (fix 1) but its
behavior on the existing test inputs is unchanged.

## Deferred to future iteration

Tests present in the input runtimes that don't yet have a preset are tracked
as GitHub issues — see the open issues on the repository.
