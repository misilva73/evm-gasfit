"""Assemble the final gas-cost proposal from fitted results and glue outputs."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from evm_gasfit.config import Config
from evm_gasfit.glue import (
    compute_glue_adjustment,
    compute_glue_opcodes_by_test,
    detect_missing_glue,
)

from .aggregate import (
    expand_to_per_client,
    select_across_client_max,
    select_per_client_max,
)
from .derived import evaluate

_log = logging.getLogger("evm_gasfit")

# Sentinel for rows that have no underlying fit — emitted when a name in
# ``proposed_by_model_params`` produced no successful regression, or when a
# derived formula resolves to ``None`` through propagation.
NO_FIT_LABEL = "<no-fit>"


@dataclass
class ProposalOutput:
    """Bundle the canonical proposal CSVs plus rendering context."""

    new_gas_all_df: pd.DataFrame
    new_gas_df: pd.DataFrame
    derived_rows: pd.DataFrame
    current_values: dict[str, int]
    warnings: list[str]
    missing_glue_pairs: list[tuple[str, str]]
    glue_opcodes_by_test_df: pd.DataFrame = field(default_factory=pd.DataFrame)


def _empty_glue_opcodes_by_test() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["test_name", "target_opcode", "glue_opcode", "corr", "ratio"]
    )


def build_proposal(
    config: Config,
    results_df: pd.DataFrame,
    glue_estimate_output=None,
    fixtures_df: pd.DataFrame | None = None,
) -> ProposalOutput:
    """Build the full proposal pipeline (aggregation + derived + diff baseline)."""
    warnings_list: list[str] = list(config.warnings)
    missing_glue_pairs: list[tuple[str, str]] = []
    glue_opcodes_by_test_df = _empty_glue_opcodes_by_test()
    glue_adjustment_df: pd.DataFrame | None = None

    glue_enabled = config.glue_adjustment.enabled and glue_estimate_output is not None
    if glue_enabled and fixtures_df is not None:
        glue_opcodes_by_test_df = compute_glue_opcodes_by_test(
            fixtures_df,
            config.resolved_models,
            config.glue_adjustment.ratio_corr_eps,
        )
        glue_adjustment_df = compute_glue_adjustment(
            results_df,
            glue_estimate_output.results_df,
            glue_opcodes_by_test_df,
            config.glue_adjustment.glue_contribution_p_value_threshold,
        )
        missing_glue_pairs = detect_missing_glue(
            fixtures_df,
            config.resolved_models,
            config.glue_adjustment.ratio_corr_eps,
        )
        for test_name, glue_opcode in missing_glue_pairs:
            msg = (
                f"missing-glue: test_name={test_name!r} correlates with non-priced "
                f"opcode {glue_opcode!r}; target coefficient left unadjusted"
            )
            _log.warning(msg)
            warnings_list.append(msg)

    expanded_df = expand_to_per_client(results_df, config, glue_adjustment_df)
    per_client_df = select_per_client_max(
        expanded_df, config.modeling.poor_fit_p_value_threshold
    )
    # ``new_gas_all_params.csv`` is the per-client max selection (one row per
    # ``(gas_param, client_name)``); also publishes ``selected_*`` aliases so
    # downstream provenance checks can match either naming.
    new_gas_all_df = per_client_df.copy()
    new_gas_all_df["selected_test"] = new_gas_all_df["test_name"]
    new_gas_all_df["selected_opcode"] = new_gas_all_df["target_opcode"]
    new_gas_all_df["selected_model_coef_name"] = new_gas_all_df["model_coef_name"]
    new_gas_df = select_across_client_max(per_client_df)

    model_by_cols = [
        c
        for c in new_gas_all_df.columns
        if c
        not in {
            "gas_param",
            "client_name",
            "runtime_ms",
            "pvalue",
            "conf_int_low",
            "conf_int_high",
            "test_name",
            "target_opcode",
            "model_coef_name",
            "glue_adjustment",
            "new_gas_decimal",
            "new_gas_rounded",
            "poor_fit",
        }
    ]

    # Placeholder rows for proposed names with no successful fit.
    expected_params: set[str] = {
        v for spec in config.resolved_models for v in spec.model_params.values()
    }
    fitted_params: set[str] = set(new_gas_df["gas_param"].astype(str))
    missing_params = sorted(expected_params - fitted_params)
    for name in missing_params:
        new_gas_df = pd.concat(
            [
                new_gas_df,
                pd.DataFrame(
                    [_no_fit_summary_row(name, new_gas_df.columns, model_by_cols)]
                ),
            ],
            ignore_index=True,
        )
        new_gas_all_df = pd.concat(
            [
                new_gas_all_df,
                pd.DataFrame(
                    [_no_fit_all_row(name, new_gas_all_df.columns, model_by_cols)]
                ),
            ],
            ignore_index=True,
        )

    # Derived parameters. Evaluated against the integer worst-case table; the
    # env carries ``None`` for unresolved names so derived formulas propagate.
    env: dict[str, int | float | None] = {}
    for _, row in new_gas_df.iterrows():
        gp = str(row["gas_param"])
        rounded = row["new_gas_rounded"]
        env[gp] = None if pd.isna(rounded) else int(rounded)

    derived_rows: list[dict[str, object]] = []
    for name, (_raw, tree) in config.derived_evaluated.items():
        value = evaluate(tree, env)
        rounded: int | None = None if value is None else math.ceil(value)
        env[name] = rounded

        derived_summary = _derived_summary_row(
            name, value, rounded, new_gas_df.columns, model_by_cols
        )
        new_gas_df = pd.concat(
            [new_gas_df, pd.DataFrame([derived_summary])], ignore_index=True
        )

        all_row = _derived_all_row(
            name, value, rounded, new_gas_all_df.columns, model_by_cols
        )
        new_gas_all_df = pd.concat(
            [new_gas_all_df, pd.DataFrame([all_row])], ignore_index=True
        )
        derived_rows.append(derived_summary)

    derived_rows_df = pd.DataFrame(derived_rows) if derived_rows else pd.DataFrame()

    # Patched fork values augmented with any integer new_params defaults are
    # the diff baseline rendered as ``current_gas`` in the proposal report.
    current_values: dict[str, int] = (
        dict(config.gas_costs_obj.values) if config.gas_costs_obj else {}
    )
    for name, value in config.new_params.items():
        if value is not None:
            current_values[name] = int(value)

    # Coerce ``new_gas_rounded`` to a nullable integer column so the empty
    # cells in placeholder rows survive CSV round-trips.
    new_gas_df["new_gas_rounded"] = new_gas_df["new_gas_rounded"].astype("Int64")
    new_gas_all_df["new_gas_rounded"] = new_gas_all_df["new_gas_rounded"].astype(
        "Int64"
    )

    # Final sort + reset for determinism.
    new_gas_all_df = new_gas_all_df.sort_values(
        ["gas_param", "client_name", "test_name", "target_opcode", "model_coef_name"],
        kind="mergesort",
    ).reset_index(drop=True)
    new_gas_df = new_gas_df.sort_values("gas_param", kind="mergesort").reset_index(
        drop=True
    )
    _ = np  # quiet linters; numpy imported for future use.

    return ProposalOutput(
        new_gas_all_df=new_gas_all_df,
        new_gas_df=new_gas_df,
        derived_rows=derived_rows_df,
        current_values=current_values,
        warnings=warnings_list,
        missing_glue_pairs=missing_glue_pairs,
        glue_opcodes_by_test_df=glue_opcodes_by_test_df,
    )


def _no_fit_summary_row(
    name: str, columns, model_by_cols: list[str]
) -> dict[str, object]:
    row: dict[str, object] = {
        "gas_param": name,
        "client_name": "",
        "runtime_ms": float("nan"),
        "conf_int_low": float("nan"),
        "conf_int_high": float("nan"),
        "selected_test": NO_FIT_LABEL,
        "selected_opcode": NO_FIT_LABEL,
        "selected_model_coef_name": NO_FIT_LABEL,
        "glue_adjustment": float("nan"),
        "new_gas_decimal": float("nan"),
        "new_gas_rounded": pd.NA,
    }
    for col in model_by_cols:
        row[col] = None
    return {c: row.get(c) for c in columns}


def _no_fit_all_row(name: str, columns, model_by_cols: list[str]) -> dict[str, object]:
    row: dict[str, object] = {
        "gas_param": name,
        "client_name": "",
        "runtime_ms": float("nan"),
        "pvalue": float("nan"),
        "conf_int_low": float("nan"),
        "conf_int_high": float("nan"),
        "test_name": NO_FIT_LABEL,
        "target_opcode": NO_FIT_LABEL,
        "model_coef_name": NO_FIT_LABEL,
        "selected_test": NO_FIT_LABEL,
        "selected_opcode": NO_FIT_LABEL,
        "selected_model_coef_name": NO_FIT_LABEL,
        "glue_adjustment": float("nan"),
        "new_gas_decimal": float("nan"),
        "new_gas_rounded": pd.NA,
        "poor_fit": False,
    }
    for col in model_by_cols:
        row[col] = None
    return {c: row.get(c) for c in columns}


def _derived_summary_row(
    name: str,
    value: float | None,
    rounded: int | None,
    columns,
    model_by_cols: list[str],
) -> dict[str, object]:
    label = NO_FIT_LABEL if value is None else "<derived>"
    row: dict[str, object] = {
        "gas_param": name,
        "client_name": "",
        "runtime_ms": float("nan"),
        "conf_int_low": float("nan"),
        "conf_int_high": float("nan"),
        "selected_test": label,
        "selected_opcode": label,
        "selected_model_coef_name": label,
        "glue_adjustment": float("nan") if value is None else 0.0,
        "new_gas_decimal": float("nan") if value is None else float(value),
        "new_gas_rounded": pd.NA if rounded is None else int(rounded),
    }
    for col in model_by_cols:
        row[col] = None
    return {c: row.get(c) for c in columns}


def _derived_all_row(
    name: str,
    value: float | None,
    rounded: int | None,
    columns,
    model_by_cols: list[str],
) -> dict[str, object]:
    label = NO_FIT_LABEL if value is None else "<derived>"
    row: dict[str, object] = {
        "gas_param": name,
        "client_name": "",
        "runtime_ms": float("nan"),
        "pvalue": float("nan"),
        "conf_int_low": float("nan"),
        "conf_int_high": float("nan"),
        "test_name": label,
        "target_opcode": label,
        "model_coef_name": label,
        "selected_test": label,
        "selected_opcode": label,
        "selected_model_coef_name": label,
        "glue_adjustment": float("nan") if value is None else 0.0,
        "new_gas_decimal": float("nan") if value is None else float(value),
        "new_gas_rounded": pd.NA if rounded is None else int(rounded),
        "poor_fit": False,
    }
    for col in model_by_cols:
        row[col] = None
    return {c: row.get(c) for c in columns}
