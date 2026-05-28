# `evm-gasfit` — e2e testing plan

This document is the contract between the implementation plan (`.claude/implementation_plan.md`)
and the e2e suite under [tests/](../tests/). It enumerates every test file, every
test function, and the plan rules each one pins down. When a behavior rule in
the plan moves, the corresponding tests here move with it.

Tests are the executable spec. There is no production code yet; each test fails
until the pipeline piece it exercises lands.

---

## 1. Shared infrastructure

| File | Purpose |
| --- | --- |
| [`tests/conftest.py`](../tests/conftest.py) | Adds the tests dir to `sys.path` so `_data_synth` is importable. No fixtures shared at the package level — every test uses `tmp_path`. |
| [`tests/_data_synth.py`](../tests/_data_synth.py) | Single source of synthesized inputs, canonical config builder, run helper, output-column constants, and small assertion helpers. |

### `_data_synth.py` exports

- **Fixture builders**: `make_block_limit_fixtures(...)`, `cross_product_fixtures(...)`, `make_glue_driver_fixtures(...)`. The last walks `evm_gasfit.glue.required.PRICED_GLUE_SPECS`, generating one block-limit sweep per family member (so `DUP` yields 16, `PUSH` yields 32). Specs without a driver test (`POP`, `STOP`) are skipped.
- **Inputs + config**: `base_config(...)` (canonical YAML dict with defaults — `models_custom` defaults to the `test_arithmetic`/`ADD` happy spec), `write_standard_inputs(tmp_path, fixtures, models, config, ...)` returns `(config_yaml, runtimes_csv, opcounts_json, out_dir)`.
- **Pipeline runner**: `run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir, *, glue=False)` drives the full public API.
- **Column constants**: `RESULTS_COLUMNS`, `NEW_GAS_ALL_PARAMS_COLUMNS`, `NEW_GAS_COLUMNS`, `ALWAYS_ON_ARTIFACTS`, `GLUE_ARTIFACTS`. Tests import these instead of restating column sets locally.
- **Assertion helpers**: `assert_columns(df, expected, label)`, `assert_sentinel_near(text, needle, sentinel, window_chars=200)`.

### `_data_synth.py` invariants

- runtimes CSV column is **`test_runtime_ms`** (LHS of every regression).
- Fixture-name token convention emits `block_limit_million_{N}` (the parser produces a `block_limit_million` column for every row).
- opcounts JSON writes both `opcount` and `data[fixture][TARGET_OPCODE] = opcount` (the equality is enforced at model estimation, once each spec has resolved its `target_opcode`).
- `runtime = intercept + slope·opcount + Σ_i extra_coef_i · opcount · param_i` matches the multi-feature regression form.

### Conventions every test follows

- `pathlib.Path` for all filesystem paths.
- `from __future__ import annotations` and PEP 604 unions.
- No mentions of "Claude", "AI", "the plan", or `.claude/` in test files.
- No mocks for scipy/pandas/numpy — the real stack runs end to end.
- Column conventions (single source of truth):

| Domain | Plan ref | Column |
| --- | --- | --- |
| runtimes CSV | §2.2 | `test_runtime_ms` |
| `results.csv` coef (ms) | §4.3 | `intercept_runtime_ms`, `target_coef_runtime_ms`, `<param>_runtime_ms` |
| `results.csv` coef stats | §4.3 | `intercept_pvalue`, `target_coef_pvalue`, `target_coef_conf_int_low/high`, `<param>_pvalue`, `<param>_conf_int_low/high` |
| `new_gas.csv` / `new_gas_all_params.csv` value | §4.7 | `new_gas_decimal` (full) + `new_gas_rounded` (`ceil`) |
| `glue_results.csv` per-fit runtime | §5 | `glue_runtime_ms` |
| YAML `models:` shape | §2.1, §2.6 | `{presets: [...], custom: [...]}` — at least one non-empty |
| Time-unit conversion | §4.5 | `new_gas_decimal = anchor_rate · runtime_ms / 1000` |

---

## 2. Test files

Each subsection lists (a) the plan rules pinned, (b) the test functions, and (c) any test-specific input notes. All tests build inputs via the `_data_synth` helpers — local fixtures are only described when they vary the standard shape.

### 2.1 [`tests/test_e2e_cli.py`](../tests/test_e2e_cli.py) — CLI exit-code contract (§8)

Plan rules pinned: §8 exit codes 0/1/2; §2.5 mapping of validation outcomes onto exit codes; §4.0 `ConfigError`/`ModelingError` boundary.

Drives the CLI via either the installed `evm-gasfit` script or `python -m evm_gasfit.cli`.

| Test | Exit | Setup |
| --- | --- | --- |
| `test_cli_run_succeeds_and_populates_out` | 0 | Valid invocation; assert every always-on artifact in §5 exists, plus light schema checks. |
| `test_cli_missing_input_returns_exit_code_1` | 1 | `--runtimes` points at a nonexistent path. |
| `test_cli_unknown_override_key_returns_exit_code_1` | 1 | `gas_costs.overrides: {NOT_A_REAL_FIELD: 123}` — strict §2.5 rule: unknown override key = hard `ConfigError`. |
| `test_cli_no_fits_produces_modeling_error_exit_2` | 2 | Single spec with `filter_by: ["opcode_NEVERMATCHES"]` excludes every fixture; per §4.3 the spec is skipped → no rows in `results.csv` → §4.0 `ModelingError`. |

### 2.2 [`tests/test_e2e_happy_path.py`](../tests/test_e2e_happy_path.py) — minimal pipeline (§2–§5)

Plan rules pinned: §2.1 config shape; §2.2/§2.3 input loaders; §4.2 single NNLS pass per `(spec, group, client)`; §4.3 `results.csv` schema; §4.5 time conversion; §4.6 worst-case across clients + winning-row provenance; §4.7 rounding; §5 always-on artifacts and glue/plots negative checks.

| Test | Coverage |
| --- | --- |
| `test_minimal_pipeline_end_to_end` | One spec (`test_arithmetic` → `OPCODE_ADD`), two clients with distinct slopes, glue off, plots off. Asserts: always-on artifacts; no glue or fig artifacts; `results.csv` column set + 2 rows + slope-within-5% recovery + `rsquared > 0.95`; `new_gas_all_params.csv` schema + uniformly-zero `glue_adjustment` + boolean `poor_fit`; `new_gas.csv` schema + worst-case row equals one row in `new_gas_all_params.csv` verbatim (§4.6 provenance); `new_gas_decimal = anchor_rate · runtime_ms / 1000`; `new_gas_rounded == ceil(new_gas_decimal)`. |

### 2.3 [`tests/test_e2e_multi_model.py`](../tests/test_e2e_multi_model.py) — multi-spec, groups, multi-feature (§2.1, §4.3, §4.6, §5.1)

Plan rules pinned: `target_operation` vs `target_operation_param`; `model_by` grouping; multi-feature regression; figure naming with `__` join separator and per-segment sanitization; `new_gas` worst-case-across-clients copies a single `new_gas_all_params` row verbatim.

| Test | Coverage |
| --- | --- |
| `test_multi_model_with_target_param_and_groups` | Two specs run together: (a) `test_arithmetic` with `target_operation_param: opcode` and `model_by: opcode` over `{ADD, SUB, MUL}` → `OPCODE_GENERIC`; (b) `test_account_access` with `target_operation: BALANCE` and `model_by: cache_strategy` over `{NO_CACHE, HOT}` → `GAS_WARM_ACCESS`. Two clients, `output.plots: true`. Asserts: `results.csv` has `(3+2) × 2 = 10` rows; the `opcode` and `cache_strategy` columns appear; every fit's `target_coef_runtime_ms` is within 5% of the planted slope; PNGs exist under `figs/runtime/` and each filename splits into exactly five `__`-joined segments matching the §5.1 contract; runtime report embeds `figs/runtime/`; `new_gas.csv` carries both gas params and the worst-case row equals exactly one row in `new_gas_all_params.csv` on the provenance columns. |
| `test_multi_feature_regression_recovers_extra_coefficient` | One spec with `model_params: {target_coef: COLD_STORAGE_WRITE, value_sent: STORAGE_WRITE_PER_VALUE}` and a cross product of `value_sent ∈ {"1","5","10"}` × the block-limit grid. Asserts: 1 row in `results.csv`; `target_coef_runtime_ms` ≈ 1.0e-5 and `value_sent_runtime_ms` ≈ 2.0e-6 (both within 5%); the per-feature stat columns are present; `new_gas.csv` carries both gas params. |

Note: the PNG-count assertion was relaxed from `len == 30` (a `3 specs × 2 clients × 3 families` arithmetic check) to "PNGs exist" + per-filename naming check, which is the actual contract. The spot-check on `ADD__test_arithmetic__ADD__geth__{family}.png` still pins the family names.

### 2.4 [`tests/test_e2e_glue.py`](../tests/test_e2e_glue.py) — glue adjustment (§4.4)

Plan rules pinned: glue off by default; the priced canonical-name set is sourced from `evm_gasfit.glue.required.PRICED_GLUE_SPECS` (skipping specs with `test_name=None`); the required-driver-fixture check; family aggregation collapses `DUP1`..`DUP16` etc. into one canonical row.

| Test | Coverage |
| --- | --- |
| `test_glue_disabled_skips_files` | Glue toggle omitted (defaults off). Asserts: `glue_results.csv`, `glue_opcodes_by_test.csv`, `glue_opcodes_autogenerated_report.md` absent; `new_gas_all_params.csv` keeps a `glue_adjustment` column that is uniformly zero. |
| `test_glue_enabled_writes_files_and_adjusts_slope` | Derives the active priced-opcode set from `PRICED_GLUE_SPECS` (POP/STOP excluded since they have no driver). Synthesizes main-model fixtures with an `ISZERO` background plus driver fixtures for every spec with a driver via `make_glue_driver_fixtures()`. Enables glue with p-value threshold 0.05. Asserts: glue CSVs + glue markdown exist; `glue_results.csv` columns ⊇ `{client_name, glue_opcode, nobs, glue_runtime_ms, p_value, rsquared}`; the `glue_opcode` set equals the active priced set; `new_gas_all_params.csv` row for `OPCODE_ADD` has `glue_adjustment > 0` and `new_gas_decimal = anchor_rate · runtime_ms / 1000` still holds with the post-adjustment `runtime_ms`. |
| `test_glue_family_collapses_to_single_canonical_row` | Confirms `DUP1`..`DUP16` driver fixtures collapse to exactly one `glue_opcode == "DUP"` row per client (no per-member leakage) and that the recovered slope matches the synthetic model. |
| `test_glue_missing_required_test_raises` | Glue enabled but no glue-required-test fixtures. Asserts any failure surfacing during load or pre-estimation (`pytest.raises(Exception)`). |
| `test_glue_missing_optional_driver_does_not_raise` | Driver fixtures present for every required spec; POP/STOP remain absent (they have no driver in any dataset). Asserts: pipeline runs to completion; POP/STOP are absent from `glue_results.csv` rather than appearing as NaN rows. |

### 2.5 [`tests/test_e2e_overrides_derived_plots.py`](../tests/test_e2e_overrides_derived_plots.py) — overrides, derived params, plots toggle (§2.4, §4.7, §4.8, §5.1)

Plan rules pinned: `gas_costs.overrides` patches the fork defaults and flows to the proposal diff; both `derived:` forms (alias and `{formula: ...}`) evaluate against the worst-case integer table in declaration order; identifier-not-found inside a derived formula is a load-time error (§4.8); the plots-on / plots-off branches in §5.1.

| Test | Coverage |
| --- | --- |
| `test_gas_overrides_flow_to_proposal` | Patches `COLD_STORAGE_ACCESS` to `9999`; the value appears in `new_gas_proposal.md`. |
| `test_derived_alias_and_formula_evaluate` | Declares three derived entries (alias, formula, chained formula). Asserts all three names appear in `new_gas.csv`, and each rounded value satisfies the §4.8 evaluation rule (integer worst-case table, `ceil`). |
| `test_plots_toggle[True]` / `test_plots_toggle[False]` | Parametrized. `plots: true` writes PNGs under `figs/runtime/` and the runtime report embeds at least one `![…]` markdown image. `plots: false` skips figs and embeds. |
| `test_derived_formula_unknown_identifier_is_load_time_error` | A `derived:` formula identifier that doesn't resolve at its declaration point (typo `COLD_STORAGE_WIRTE`) raises at `GasFit.from_config(...)` — not later. |

### 2.6 [`tests/test_e2e_presets.py`](../tests/test_e2e_presets.py) — model preset registry (§2.6)

Plan rules pinned: a preset is a named, frozen `ModelSpec` shipped in `defaults/models.py`; `models.presets:` is resolved at load time and concatenated with `models.custom:`; selection is per-preset, never field-level; unknown preset = config error; duplicate `test_name` across `presets:` and `custom:` is allowed (independent fits); empty `presets:` *and* empty `custom:` = config error.

The canonical preset under test is `arithmetic_add` (`test_arithmetic` → `ADD` → `OPCODE_ADD`).

| Test | Coverage |
| --- | --- |
| `test_preset_only_config_runs_pipeline` | `models: {presets: ["arithmetic_add"], custom: []}` runs the full pipeline; `OPCODE_ADD` appears in `new_gas.csv`. |
| `test_preset_plus_custom_concatenate` | One preset + one custom (different `test_name` / target); both gas params appear in `new_gas.csv`. |
| `test_invalid_models_section_is_config_error[unknown_preset_name]` / `[empty_presets_and_empty_custom]` | Parametrized: both invalid `models:` shapes raise at `GasFit.from_config(...)`. |
| `test_duplicate_test_name_across_preset_and_custom_is_allowed` | A preset and a custom spec both target `test_arithmetic`/`ADD` but write to different gas params; both rows appear in `new_gas.csv`. |

### 2.7 [`tests/test_e2e_fixture_params.py`](../tests/test_e2e_fixture_params.py) — derived fixture params (§2.7)

Plan rules pinned: `fixture_params:` materializes per-spec derived columns from raw parsed params; `source:` required, `values:` optional value remap (keys coerced to `str` at load time); derived columns live on the per-spec slice so two specs can declare the same derived name with different sources; unmapped source value = fit-time error.

| Test | Coverage |
| --- | --- |
| `test_fixture_param_rename_passes_through_numeric_value` | `fixture_params: {update: {source: value_sent}}` (rename only); the regressor sees float-coerced values; `target_coef_runtime_ms` recovers the planted slope. |
| `test_fixture_param_value_remap_translates_strings` | `fixture_params: {update: {source: write_new_value, values: {"False": 0, "True": 1}}}`; non-numeric strings are remapped to floats before the regressor consumes them. |
| `test_two_specs_can_share_derived_name_with_different_sources` | Two specs both declare `update` from different raw sources; each spec's `update` column is independent; both pipelines produce their gas params. |
| `test_unmapped_source_value_raises` | `values: {"False": 0}` but a fixture has `True`; `estimate_models()` raises at fit time. |

### 2.7a [`tests/test_e2e_bytes_to_words.py`](../tests/test_e2e_bytes_to_words.py) — `bytes_to_words` fixture-param transform (§2.7)

Plan rules pinned: a `fixture_params` entry may carry `transform: bytes_to_words` to apply `ceil(x / 32)` to a byte-sized raw param before the regression consumes it; the fitted coefficient on the derived column then directly recovers the per-word slope. `transform` and `values` are mutually exclusive.

| Test | Coverage |
| --- | --- |
| `test_bytes_to_words_transform_recovers_per_word_coefficient` | Copy-style sweep with `calldata_size ∈ {32, 64, 96, 128}`; spec declares `fixture_params.calldata_words = {source: calldata_size, transform: bytes_to_words}` and `model_params.calldata_words → OPCODE_CALLDATACOPY_PER_WORD`; the recovered `calldata_words_runtime_ms` matches the planted per-word slope; both BASE and PER_WORD gas params land in `new_gas.csv`. |
| `test_transform_and_values_are_mutually_exclusive` | A spec setting both fields raises at config load. |

### 2.7b [`tests/test_e2e_anchor_filter.py`](../tests/test_e2e_anchor_filter.py) — prefix-overlap `filter_by` anchors (§2.6 / catalog)

Plan rules pinned: when an opcode's `opcode_<X>` token is a prefix of another opcode's token in the same `test_name` slice, the catalog preset must carry a trailing `-` anchor (`filter_by: [opcode_<X>-]`) so the default substring match cannot leak the sibling's fixtures into the fit.

| Test | Coverage |
| --- | --- |
| `test_anchored_filter_excludes_prefix_sibling[add_vs_addmod]` / `[mul_vs_mulmod]` / `[push0_vs_push1]` | Parametrized over the three documented overlap pairs. Synthesizes fixtures for *both* the target opcode and the sibling under one `test_name`, drives the corresponding catalog preset (`arithmetic_add`, `arithmetic_mul`, `stack_push0`), and asserts `results.csv` carries only the target opcode and the recovered slope matches the target's planted value — not the sibling's (5× larger). |

### 2.7c [`tests/test_e2e_catalog_smoke.py`](../tests/test_e2e_catalog_smoke.py) — full preset catalog smoke (§2.6)

Plan rules pinned: every preset in `defaults/models.py::PRESETS` must pass Pydantic validation and drive an end-to-end pipeline run without raising. The goal is to catch typos in `test_name` / `target_operation` / `model_params` keys at landing time, not to validate fit quality.

| Test | Coverage |
| --- | --- |
| `test_every_catalog_preset_fits_without_raising` | Programmatically synthesizes one block-limit sweep per preset by introspecting each `ModelSpec` (covers `target_operation_param`, `model_by`, `fixture_params` sources, non-target `model_params` coefs, and precompile-style `target_operation_count_source`). Loads all preset names into one config, runs the pipeline, asserts every preset's `test_name` produces at least one row in `results.csv` and `new_gas.csv` is non-empty. |
| `test_catalog_warnings_only_list_new_gas_param_names` | Loads the catalog config and asserts every config-load warning names one of the documented new gas-param names (`OPCODE_*COPY_PER_WORD`, `COLD_ACCOUNT_NOCODE_ACCESS`, `COLD_ACCOUNT_CODE_ACCESS`, `ACCOUNT_WRITE`, `STORAGE_WRITE`) — no surprise warnings from typos. |

### 2.8 [`tests/test_e2e_determinism.py`](../tests/test_e2e_determinism.py) — determinism contract (§4.0)

Plan rules pinned: "given identical inputs and the same `random_seed`, all CSV and markdown outputs are byte-identical across runs and across platforms"; bootstrap sampling threads the seed into every `numpy.random.Generator`; PNGs are **not** promised byte-identical.

| Test | Coverage |
| --- | --- |
| `test_two_runs_with_same_seed_produce_byte_identical_csvs_and_md` | Two pipeline runs with the same inputs and `random_seed: 42` into distinct `out_dir`s; every always-on CSV and MD compares byte-equal via `Path.read_bytes()`. Markdown artifacts are normalized through `_normalize_for_compare` first — the `_Generated YYYY-MM-DD HH:MM:SSZ` header in `new_gas_proposal.md` is wall-clock metadata, not computed content, and is replaced with `_Generated <TIMESTAMP>` before comparison. |
| `test_different_seed_produces_different_bootstrap_outputs` | Seeds 42 vs 99 against identical inputs; at least one of `target_coef_conf_int_low/high` or `target_coef_pvalue` differs. |
| `test_glue_on_pipeline_is_also_deterministic` | Same byte-equality check as test 1 but with glue enabled, so `glue_results.csv`, `glue_opcodes_by_test.csv`, and `glue_opcodes_autogenerated_report.md` are also compared. |

### 2.9 [`tests/test_e2e_proposal_warnings.py`](../tests/test_e2e_proposal_warnings.py) — warnings + sentinel rendering (§2.5, §4.0, §4.4, §4.6)

Plan rules pinned (merged from three earlier files — see §3):

- §2.5 lenient `model_params` RHS naming an unknown gas-param: pipeline runs, warning fires on the `evm_gasfit` logger, the name appears in `new_gas.csv` + the Warnings section of `new_gas_proposal.md`.
- §4.6 "no prior default" sentinel co-located with the unknown name in either CSV or markdown; no isolated `| 0 |` table cell; a known fork field must NOT carry the sentinel.
- §4.4 missing-glue detection: a non-priced opcode meeting corr/ratio thresholds emits a warning and surfaces in the proposal; a priced opcode does not.

| Test | Coverage |
| --- | --- |
| `test_unknown_gas_param_emits_warning_and_renders_sentinel[BRAND_NEW_GAS_PARAM]` / `[OPCODE_NEVER_EXISTED_GAS]` | Parametrized over two distinct unknown gas-param names so each independently catches a regression. Single spec, two clients. Asserts: pipeline completes; `caplog.at_level(logging.WARNING, logger="evm_gasfit")` captures a WARNING mentioning the param; row in `new_gas.csv`; Warnings heading in proposal mentions the param; `assert_sentinel_near(...)` finds `no prior default` within 200 chars of the name; no `\|\s*0\s*\|` table cell on rows mentioning the name; any present `current_gas`/`diff`/`gas_diff` column is not numeric 0. |
| `test_known_gas_param_does_not_carry_sentinel` | Counterpart with `OPCODE_ADD`. Asserts the sentinel does NOT appear in the CSV row text or any markdown line mentioning `OPCODE_ADD`. |
| `test_non_priced_glue_candidate_emits_warning_and_appears_in_proposal` | Main `test_arithmetic` / `ADD` fit contaminated with `ADDMOD` counts exactly proportional to the target opcount (corr ≈ 1.0), plus `make_glue_driver_fixtures()`. Glue enabled. Asserts: derived priced set from `PRICED_GLUE_OPCODES` does NOT contain `ADDMOD`; at least one WARNING on an `evm_gasfit`-namespaced logger naming `ADDMOD`; `ADDMOD` appears in `new_gas_proposal.md`. |
| `test_priced_glue_opcode_does_not_emit_missing_glue_warning` | Sanity counterpart with `POP` (priced) as the contaminant. Asserts no `evm_gasfit`-namespaced WARNING names `POP`. |

### 2.10 [`tests/test_e2e_report_format.py`](../tests/test_e2e_report_format.py) — `new_gas_proposal.md` formatting + structural contract (§5)

Plan rules pinned: the headline ordering, column headers, diff-cell rendering, sentinel handling, anchor-rate formatting, gas-param row ordering, and the heatmap embed location described in §5's `new_gas_proposal.md` row. The proposal heatmap colormap rule (`log2(proposed / current)` with `RdYlGn_r` and a blank row for `null` baselines) is exercised end-to-end here.

| Test | Coverage |
| --- | --- |
| `test_anchor_rate_renders_as_mgas_per_second` / `test_anchor_rate_three_sig_fig_smart_format` | `anchor_rate` in the run-metadata line renders as `<N> Mgas/s` with 3 significant figures (`1.0e8 → 100 Mgas/s`, `1.234e8 → 123 Mgas/s`). |
| `test_proposed_table_column_order_and_headers` | `## Proposed gas parameters` table columns are `Gas param \| Current gas \| Proposed gas \| Diff \| Diff %` in that order, title-cased. |
| `test_diff_percent_column_computed_against_current` | Each fitted row's `Diff %` cell equals `round((proposed - current) / current * 100)` with a signed integer percent. |
| `test_new_param_diff_pct_renders_na` | A `new_params: {NAME: null}` row renders `no prior default` in `Current gas` and `n/a` in both `Diff` and `Diff %` (no current baseline to ratio against). |
| `test_client_comparison_section_present_and_populated` | `## Client comparison` sits between `## Proposed gas parameters` and `## Warnings`; its header row matches the documented column order; rows carry worst-then-second-worst clients with a `Ratio` cell (`worst gas / second-worst gas`) formatted as `1.23×`. |
| `test_heatmap_embedded_in_client_comparison` | When `output.plots: true`, the `![](figs/proposal/heatmap.png)` embed lives inside the `## Client comparison` section (no trailing `## Plots` heading, no `by_client.png`). |
| `test_null_baseline_param_in_heatmap_emits_warning` | A model writing to a `new_params: {NAME: null}` entry with plots enabled surfaces a `null-baseline: new_params['NAME']` warning under `## Warnings` and still produces the heatmap PNG (that row renders blank, annotations only). |
| `test_null_baseline_warning_absent_when_all_baselines_known` | The standard config (every gas param backed by a raw fork field) emits no `null-baseline:` warning — the new warning is gated on actual null-baseline declarations. |
| `test_partial_fits_subsection_empty_renders_none` / `test_partial_fits_subsection_lists_missing_client_combos` | `### Partial fits (missing clients)` is always present inside `## Warnings`, renders `_None._` when every gas param fits on every client, and lists `\| gas_param \| missing_clients \|` rows when a param fits on some clients but not others. |
| `test_gas_params_follow_config_declaration_order` | Proposed-params table, client-comparison table, and `new_gas.csv` list gas params in the order each first appears in `models.custom` (then `derived` keys) — not alphabetically. Picks `SUB, ADD, MUL` as non-alphabetical opcodes to make the assertion meaningful. |
| `test_provenance_subsection_present_with_per_param_heatmaps` | When `output.plots: true` and at least one gas param has ≥ 2 distinct model combos, a `### Worst-case provenance per gas param` subsection sits inside `## Client comparison` (after the overview heatmap embed, before `## Warnings`). Each qualifying gas param renders a `<details>` block whose `<summary>` carries the param name and whose body embeds `figs/proposal/provenance__<gas_param>.png`. The PNG exists for each qualifying param. |
| `test_provenance_subsection_skips_single_combo_params` | Gas params with only one distinct combo (e.g. a derived alias, or a param fit by a single spec on a single client) do *not* get a `<details>` block; they are listed in a single italic line at the top of the subsection. No provenance PNG is written for them. |
| `test_provenance_subsection_omitted_when_plots_disabled` | `output.plots: false` runs do not produce the `### Worst-case provenance per gas param` subsection nor any `provenance__*.png` figures, even when multiple combos are present. |

---

## 3. Maintenance log (refactor notes)

Earlier waves of the suite split warnings, sentinel rendering, and missing-glue detection into three sibling files for "fail-independence." That guarantee is now provided by `@pytest.mark.parametrize` on a single test surface, so [`test_e2e_proposal_warnings.py`](../tests/test_e2e_proposal_warnings.py) carries all three concerns. Each test still fails in isolation if the corresponding implementation surface drifts.

Several rules previously left as "open consistency questions" remain open at the assertion boundary:

- `glue_results.csv` client column name (`client_name` assumed, not `client`).
- `runtime_ms` semantics on `new_gas_all_params.csv` post-adjustment when glue is on (the §4.5 conversion is treated as the binding contract).
- `ModelingError` granularity for empty-spec-only runs (CLI test asserts exit 2).
- `poor_fit` dtype (boolean or stringified bool — assertion accepts either).
- `evm_gasfit` package logger naming (tests scope `caplog` to that logger or descendants).
- "no prior default" literal phrasing (case-insensitive substring match).
- Warnings section heading depth in `new_gas_proposal.md` (matches `#+\s*Warnings`).
- `new_gas.csv` diff/current-default column names (tests are defensive across both CSV and markdown).

When the implementation pins any of the above, tighten the matching assertion to the exact value.

---

## 4. Coverage matrix

| Plan section | Topic | Pinned by |
| --- | --- | --- |
| §2.1 | YAML config shape, model specs | `test_e2e_happy_path`, `test_e2e_multi_model` |
| §2.2 | Runtimes CSV columns | `_data_synth` + every test |
| §2.3 | Opcounts JSON + `opcount == data[fixture][TARGET]` invariant | `_data_synth` enforces |
| §2.4 | Gas-cost defaults, overrides mechanics | `test_gas_overrides_flow_to_proposal` |
| §2.5 | Strict override-key validation; lenient `model_params`/`derived:` warnings | `test_cli_unknown_override_key_returns_exit_code_1` (strict); `test_unknown_gas_param_emits_warning_and_renders_sentinel` (lenient, parametrized) |
| §2.6 | Model presets registry | `test_e2e_presets` (4 tests inc. parametrized); `test_e2e_anchor_filter` (3 parametrized — prefix-overlap anchors); `test_e2e_catalog_smoke` (2 tests — full PRESETS dict) |
| §2.7 | Derived fixture params (per-spec rename + value remap + `bytes_to_words` transform) | `test_e2e_fixture_params` (4 tests); `test_e2e_bytes_to_words` (2 tests) |
| §3 | Pipeline architecture | implicit via every happy-path test |
| §4.0 | Logging channel, error types, determinism | CLI exit codes + `test_e2e_determinism` (3 tests) |
| §4.1 | Fixture-name parser | implicit via every test |
| §4.2 | NNLS regressor, fit failure modes | regressor covered; explicit failure modes (rank-deficient, constant opcount, scipy raise) deferred to unit tests |
| §4.3 | Model formula, `results.csv` schema | `test_e2e_happy_path`, `test_e2e_multi_model` |
| §4.4 | Glue-opcode adjustment + missing-glue warning | `test_e2e_glue` (3 tests); `test_non_priced_glue_candidate_*` + counter-test |
| §4.5 | Time units / gas conversion | `test_e2e_happy_path`, `test_e2e_glue` |
| §4.6 | Per-client → across-client aggregation; winning-row provenance; "no prior default" sentinel | `test_e2e_happy_path`, `test_e2e_multi_model`; sentinel tests in `test_e2e_proposal_warnings` |
| §4.7 | `new_gas_decimal` + `new_gas_rounded` rounding | `test_e2e_happy_path`, `test_derived_alias_and_formula_evaluate` |
| §4.8 | Derived params: alias, formula, AST whitelist, load-time identifier check | `test_derived_alias_and_formula_evaluate`, `test_derived_formula_unknown_identifier_is_load_time_error` |
| §5 / §5.1 | Output artifacts table; figure naming/layout; plots toggle | multiple |
| §5 (`new_gas_proposal.md`) | Section ordering, column headers, anchor-rate formatting, sentinel rendering, gas-param row order, heatmap colormap (`log2(proposed / current)` + `null`-baseline blank rows) | `test_e2e_report_format` (11 tests) |
| §8 | CLI contract, exit codes | `test_e2e_cli` (4 tests) |
| §11 | Docs site | out of scope for the e2e suite |

---

## 5. Deferred (unit-test material)

The following sad paths are intentionally not e2e tests. They exercise a single module's behavior and belong as unit tests once `evm_gasfit/io/`, `evm_gasfit/modeling/`, and `evm_gasfit/proposal/` stabilize.

- §4.2 fit failure modes — rank-deficient design, constant `opcount`, scipy convergence failure — each skip a fit with a warning, run continues.
- §4.6 tie-break order (per-client `runtime_ms`, then `pvalue`, then lexicographic; across-client by ascending `client_name`).
- §2.4 gas-cost source selection (`ethereum/execution-specs` vs. `_fallback.py`) and the `EVM_GASFIT_USE_FALLBACK=1` env-var override.

---

## 6. How to maintain this document

- When you add or rename a test, update its row in §2 and the matching cell in §4.
- When the plan moves (a column renames, a section is added), update the Conventions table in §1 first, then push the change through to every matching assertion. Tests with broken assertions are the safety net for plan/code drift — don't relax assertions to make them pass; surface the conflict to the spec.
- When adding boilerplate (a new config shape, a new fixture pattern), prefer extending `_data_synth.py` over duplicating in test files. The factory/runner/columns trio is the single source of truth.
