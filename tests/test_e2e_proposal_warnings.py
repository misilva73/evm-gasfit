"""End-to-end: warnings + sentinel rendering on the proposal.

Three related rules share a single test surface because they all hinge on
the same proposal-rendering path:

- **Lenient unknown gas-param**: a ``model_params`` RHS naming a gas-param
  that the fork's ``GasCosts`` does not define is allowed (the whole point
  of the tool is to propose new prices). The pipeline runs, a warning fires
  on the ``evm_gasfit`` logger, and the new name appears in the Warnings
  section of ``new_gas_proposal.md``.
- **"no prior default" sentinel**: the same unknown-name row renders the
  ``no prior default`` sentinel in either ``new_gas.csv`` or
  ``new_gas_proposal.md`` instead of a misleading numeric zero. A known
  fork-field row must NOT carry the sentinel (regression guard).
- **Missing-glue detection**: glue detection inspects every fitted model's
  group for opcodes that meet the corr/ratio thresholds; an opcode that
  meets the thresholds but is outside the priced set triggers a warning
  and surfaces in the proposal.

The lenient-warning and sentinel paths are parametrized over two distinct
gas-param names (``BRAND_NEW_GAS_PARAM`` and ``OPCODE_NEVER_EXISTED_GAS``)
so each name independently catches a regression.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import pytest

from _data_synth import (
    ClientModel,
    assert_sentinel_near,
    base_config,
    make_block_limit_fixtures,
    make_glue_driver_fixtures,
    run_pipeline,
    write_standard_inputs,
)


SENTINEL = "no prior default"
UNKNOWN_PARAMS = ("BRAND_NEW_GAS_PARAM", "OPCODE_NEVER_EXISTED_GAS")
KNOWN_PARAM = "OPCODE_ADD"

# ----- lenient unknown gas-param ------------------------------------------


def _build_unknown_param_inputs(tmp_path: Path, gas_param: str):
    fixtures = make_block_limit_fixtures(
        test_file="test_arithmetic",
        test_name="test_arithmetic",
        target_opcode="ADD",
        params={"opcode": "ADD"},
    )
    models = {
        "geth": ClientModel(intercept=80.0, slope=1.0e-5),
        "besu": ClientModel(intercept=100.0, slope=1.5e-5),
    }
    config = base_config(
        models_custom=[
            {
                "test_name": "test_arithmetic",
                "target_operation": "ADD",
                "model_params": {"target_coef": gas_param},
            }
        ],
    )
    return write_standard_inputs(
        tmp_path, fixtures=fixtures, models=models, config=config, seed=17
    )


@pytest.mark.parametrize("gas_param", UNKNOWN_PARAMS)
def test_unknown_gas_param_emits_warning_and_renders_sentinel(
    tmp_path: Path, gas_param: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Lenient path: pipeline succeeds, warning fires on the ``evm_gasfit``
    logger, the new name appears in ``new_gas.csv`` + the proposal's
    Warnings section, and the proposal diff renders the ``no prior default``
    sentinel co-located with the param (not a fabricated numeric zero)."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_unknown_param_inputs(
        tmp_path, gas_param
    )

    with caplog.at_level(logging.WARNING, logger="evm_gasfit"):
        run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    # ---- warning fired on evm_gasfit logger ----------------------------
    matching = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and gas_param in r.getMessage()
    ]
    assert matching, (
        f"expected a WARNING-level log record mentioning {gas_param!r} on "
        f"the evm_gasfit logger; got {[r.getMessage() for r in caplog.records]!r}"
    )

    # ---- row in new_gas.csv --------------------------------------------
    new_gas = pd.read_csv(out_dir / "new_gas.csv")
    assert gas_param in set(new_gas["gas_param"]), (
        f"{gas_param} missing from new_gas.csv gas_param column"
    )

    # ---- Warnings section in the proposal names the param --------------
    proposal = (out_dir / "new_gas_proposal.md").read_text()
    warnings_heading = re.search(r"#+\s*Warnings", proposal, flags=re.IGNORECASE)
    assert warnings_heading, "new_gas_proposal.md is missing a Warnings section"
    assert gas_param in proposal[warnings_heading.start() :], (
        f"Warnings section does not mention {gas_param}"
    )

    # ---- "no prior default" sentinel co-located with the new name ------
    csv_text = new_gas[new_gas["gas_param"] == gas_param].to_csv(index=False)
    assert_sentinel_near(csv_text + "\n" + proposal, gas_param, SENTINEL)

    # ---- no isolated `| 0 |` markdown cell that would imply prior == 0 -
    md_lines = [line for line in proposal.splitlines() if gas_param in line]
    isolated_zero = re.compile(r"\|\s*0\s*\|")
    for line in md_lines:
        assert not isolated_zero.search(line), (
            f"markdown row for {gas_param} renders an isolated `0` table cell:\n{line}"
        )

    # If new_gas.csv carries a numeric diff column, that cell must not be 0.
    new_row = new_gas[new_gas["gas_param"] == gas_param].iloc[0]
    for diff_col in ("current_gas", "diff", "gas_diff"):
        if diff_col in new_gas.columns:
            cell = new_row[diff_col]
            assert not (isinstance(cell, (int, float)) and cell == 0), (
                f"{diff_col} renders a misleading 0 for {gas_param}"
            )


def test_known_gas_param_does_not_carry_sentinel(tmp_path: Path) -> None:
    """A known fork-field gas-param must not carry the sentinel — guards
    against a regression where the sentinel leaks into every row."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_unknown_param_inputs(
        tmp_path, KNOWN_PARAM
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    new_gas = pd.read_csv(out_dir / "new_gas.csv")
    assert KNOWN_PARAM in set(new_gas["gas_param"]), (
        f"{KNOWN_PARAM} missing from new_gas.csv"
    )

    csv_text = new_gas[new_gas["gas_param"] == KNOWN_PARAM].to_csv(index=False)
    proposal = (out_dir / "new_gas_proposal.md").read_text()
    md_lines = "\n".join(line for line in proposal.splitlines() if KNOWN_PARAM in line)
    row_text = f"{csv_text}\n{md_lines}"

    assert SENTINEL.lower() not in row_text.lower(), (
        f"sentinel {SENTINEL!r} leaked into a known-default row for {KNOWN_PARAM}"
    )


# ----- missing-glue detection --------------------------------------------


# A clearly non-glue opcode: arithmetic, three operands, never used as a stitch.
NON_PRICED_OPCODE = "ADDMOD"


def _build_contaminant_inputs(
    tmp_path: Path, *, contaminant: str, contaminant_per_million: float = 500_000
):
    main_fixtures = make_block_limit_fixtures(
        test_file="test_arithmetic",
        test_name="test_arithmetic",
        target_opcode="ADD",
        params={"opcode": "ADD"},
        extra_opcount_per_million={contaminant: contaminant_per_million},
    )
    all_fixtures = main_fixtures + make_glue_driver_fixtures()
    models = {"geth": ClientModel(intercept=50.0, slope=2.0e-5)}
    return write_standard_inputs(
        tmp_path,
        fixtures=all_fixtures,
        models=models,
        config=base_config(glue_enabled=True),
        seed=7,
    )


def test_non_priced_glue_candidate_emits_warning_and_appears_in_proposal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from evm_gasfit.glue.required import REQUIRED_GLUE_TESTS

    priced = {op for _, op in REQUIRED_GLUE_TESTS}
    assert NON_PRICED_OPCODE not in priced, (
        f"{NON_PRICED_OPCODE} unexpectedly priced — adjust the test contaminant"
    )

    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_contaminant_inputs(
        tmp_path, contaminant=NON_PRICED_OPCODE
    )

    with caplog.at_level(logging.WARNING, logger="evm_gasfit"):
        run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir, glue=True)

    pattern = re.compile(rf"\b{re.escape(NON_PRICED_OPCODE)}\b")
    matching = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and r.name.startswith("evm_gasfit")
        and pattern.search(r.getMessage())
    ]
    assert matching, (
        f"expected a WARNING on logger 'evm_gasfit' naming {NON_PRICED_OPCODE!r}; "
        f"got {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]}"
    )

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    assert NON_PRICED_OPCODE in proposal, (
        f"{NON_PRICED_OPCODE!r} expected to appear in new_gas_proposal.md"
    )


def test_priced_glue_opcode_does_not_emit_missing_glue_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sanity counterpart: POP is in the priced set, so even when it
    correlates perfectly with the target opcount the missing-glue warning
    does not fire."""
    priced_opcode = "POP"
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_contaminant_inputs(
        tmp_path, contaminant=priced_opcode
    )

    with caplog.at_level(logging.WARNING, logger="evm_gasfit"):
        run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir, glue=True)

    pattern = re.compile(rf"\b{re.escape(priced_opcode)}\b")
    offending = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and r.name.startswith("evm_gasfit")
        and pattern.search(r.getMessage())
    ]
    assert not offending, (
        f"unexpected WARNING(s) naming a priced glue opcode {priced_opcode!r}: "
        f"{[(r.name, r.getMessage()) for r in offending]}"
    )
