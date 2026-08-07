"""End-to-end: `overhead_baseline_param` runtime-delta pairing.

A spec can declare `overhead_baseline_param` to pair each fixture where that
param is `"False"` with its `"True"` counterpart (same params otherwise) and
fit `target_coef` on the runtime delta (`False - True`) instead of raw
`False` runtime. The `True` variant runs the same harness without the target
opcode, so the delta cancels anything the two variants share — known glue
opcode or not — without needing the per-opcode glue mechanism.

Paths exercised:

- A contaminant with an identical count in both variants (mirrors keccak on
  `test_account_access`) cancels for free; the recovered target_coef matches
  the clean planted slope rather than the slope-plus-contamination a raw fit
  on the `False` fixtures alone would recover.
- A baseline-paired spec contributes no rows to `glue_opcodes_by_test.csv`,
  even though its `False` variant is contaminated — the per-opcode glue
  mechanism must never see it (double-subtraction guard).
- A `False` fixture with no matching `True` counterpart raises `ConfigError`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from _data_synth import (
    ClientModel,
    FixtureSpec,
    base_config,
    make_glue_driver_fixtures,
    run_pipeline,
    write_standard_inputs,
)

from evm_gasfit.errors import ConfigError

_BLOCK_LIMITS = (30, 60, 90, 120, 150, 180, 210, 240)
_CONTAMINANT_PER_MILLION = 500_000.0


def _paired_fixtures(
    *, drop_true_for_block_limit: int | None = None
) -> list[FixtureSpec]:
    """One `overhead_baseline` False/True pair per block-limit sweep point.

    The contaminant opcode gets the *same* count in both variants (mirrors
    keccak being identical regardless of whether the target opcode runs).
    """
    fixtures: list[FixtureSpec] = []
    for bl in _BLOCK_LIMITS:
        contaminant_count = bl * _CONTAMINANT_PER_MILLION
        fixtures.append(
            FixtureSpec(
                test_file="test_probe_access",
                test_name="test_probe_access",
                params={"overhead_baseline": "False"},
                block_limit_million=bl,
                target_opcode="PROBEOP",
                target_opcount=bl * 1_000_000.0,
                extra_opcounts={"SHA3LIKE": contaminant_count},
            )
        )
        if bl == drop_true_for_block_limit:
            continue
        fixtures.append(
            FixtureSpec(
                test_file="test_probe_access",
                test_name="test_probe_access",
                params={"overhead_baseline": "True"},
                block_limit_million=bl,
                target_opcode="PROBEOP",
                target_opcount=0.0,
                extra_opcounts={"SHA3LIKE": contaminant_count},
            )
        )
    return fixtures


def _paired_config(**overrides) -> dict:
    return base_config(
        models_custom=[
            {
                "test_name": "test_probe_access",
                "target_operation": "PROBEOP",
                "overhead_baseline_param": "overhead_baseline",
                "model_params": {"target_coef": "OPCODE_PROBEOP"},
            }
        ],
        new_params={"OPCODE_PROBEOP": None},
        glue_enabled=True,
        **overrides,
    )


def test_baseline_pair_cancels_shared_contamination(tmp_path: Path) -> None:
    true_cost = 4.0e-5
    contam_rate = 3.0e-5  # would bias a raw (unpaired) fit if not cancelled
    fixtures = _paired_fixtures() + make_glue_driver_fixtures()
    models = {
        "geth": ClientModel(
            intercept=50.0, slope=true_cost, glue_coefs={"SHA3LIKE": contam_rate}
        )
    }
    config_yaml, runtimes_csv, opcounts_json, out_dir = write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=_paired_config(),
        seed=41,
        noise_pct=0.001,
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir, glue=True)

    results = pd.read_csv(out_dir / "results.csv")
    row = results[results["target_opcode"] == "PROBEOP"].iloc[0]
    # The paired delta cancels the shared SHA3LIKE contamination — the fitted
    # coefficient recovers the clean planted slope, not slope + contam_rate*ratio.
    assert float(row["target_coef_runtime_ms"]) == pytest.approx(true_cost, rel=0.05)

    new_gas_all = pd.read_csv(out_dir / "new_gas_all_params.csv")
    probe_row = new_gas_all[new_gas_all["gas_param"] == "OPCODE_PROBEOP"].iloc[0]
    # Never detected as glue, so never (redundantly) adjusted a second time.
    assert float(probe_row["glue_adjustment"]) == 0.0


def test_baseline_pair_aggregates_repeated_true_trials(tmp_path: Path) -> None:
    """Real benchmark suites repeat each fixture across several trials, so
    the `False` and `True` sides don't line up 1:1 by fixture identity —
    the `True` side must be averaged first, or the merge either raises
    (many-to-many) or silently fans out."""
    true_cost = 4.0e-5
    contam_rate = 3.0e-5
    single_pass = _paired_fixtures()
    # 3 repeated trials per fixture identity, independently noised.
    fixtures = single_pass * 3 + make_glue_driver_fixtures()
    models = {
        "geth": ClientModel(
            intercept=50.0, slope=true_cost, glue_coefs={"SHA3LIKE": contam_rate}
        )
    }
    config_yaml, runtimes_csv, opcounts_json, out_dir = write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=_paired_config(),
        seed=53,
        noise_pct=0.02,
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir, glue=True)

    results = pd.read_csv(out_dir / "results.csv")
    row = results[results["target_opcode"] == "PROBEOP"].iloc[0]
    assert int(row["nobs"]) == 3 * len(_BLOCK_LIMITS)
    assert float(row["target_coef_runtime_ms"]) == pytest.approx(true_cost, rel=0.1)


def test_baseline_paired_spec_excluded_from_glue_detection(tmp_path: Path) -> None:
    fixtures = _paired_fixtures() + make_glue_driver_fixtures()
    models = {
        "geth": ClientModel(
            intercept=50.0, slope=4.0e-5, glue_coefs={"SHA3LIKE": 3.0e-5}
        )
    }
    config_yaml, runtimes_csv, opcounts_json, out_dir = write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=_paired_config(),
        seed=43,
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir, glue=True)

    glue_by_test = pd.read_csv(out_dir / "glue_opcodes_by_test.csv")
    # SHA3LIKE correlates strongly with PROBEOP's raw opcount (same sweep),
    # but the spec is baseline-paired, so it must be entirely absent here.
    assert glue_by_test[glue_by_test["test_name"] == "test_probe_access"].empty


def test_baseline_pair_raises_on_unmatched_false_row(tmp_path: Path) -> None:
    fixtures = _paired_fixtures(drop_true_for_block_limit=120)
    models = {
        "geth": ClientModel(
            intercept=50.0, slope=4.0e-5, glue_coefs={"SHA3LIKE": 3.0e-5}
        )
    }
    config_yaml, runtimes_csv, opcounts_json, out_dir = write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=_paired_config(),
        seed=47,
    )
    with pytest.raises(ConfigError, match="no matching overhead_baseline_True"):
        run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir, glue=True)
