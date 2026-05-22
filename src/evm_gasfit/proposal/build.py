"""Assemble the final gas-cost proposal from fitted results and glue outputs."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from evm_gasfit.config import Config
from evm_gasfit.defaults import get_gas_costs
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


@dataclass
class ProposalOutput:
    """Bundle the canonical proposal CSVs plus rendering context."""

    new_gas_all_df: pd.DataFrame
    new_gas_df: pd.DataFrame
    derived_rows: pd.DataFrame
    raw_fork_baseline: dict[str, int]
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

    # Derived parameters. Evaluated against the integer worst-case table; updates
    # the env after each entry so chained references work.
    anchor_rate = float(config.anchor_rate)
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
    env: dict[str, int | float] = {
        str(row["gas_param"]): int(row["new_gas_rounded"])
        for _, row in new_gas_df.iterrows()
    }

    derived_rows: list[dict[str, object]] = []
    for name, (_raw, tree) in config.derived_evaluated.items():
        value = evaluate(tree, env)
        rounded = math.ceil(value)
        env[name] = rounded

        # Append to new_gas_df.
        derived_summary: dict[str, object] = {
            "gas_param": name,
            "client_name": "",
            "runtime_ms": float("nan"),
            "conf_int_low": float("nan"),
            "conf_int_high": float("nan"),
            "selected_test": "<derived>",
            "selected_opcode": "<derived>",
            "selected_model_coef_name": "<derived>",
            "glue_adjustment": 0.0,
            "new_gas_decimal": float(value),
            "new_gas_rounded": int(rounded),
        }
        for col in model_by_cols:
            derived_summary[col] = None
        derived_summary_aligned = {
            c: derived_summary.get(c) for c in new_gas_df.columns
        }
        new_gas_df = pd.concat(
            [new_gas_df, pd.DataFrame([derived_summary_aligned])], ignore_index=True
        )

        # Append a matching row to new_gas_all_df.
        all_row: dict[str, object] = {
            "gas_param": name,
            "client_name": "",
            "runtime_ms": float("nan"),
            "pvalue": float("nan"),
            "conf_int_low": float("nan"),
            "conf_int_high": float("nan"),
            "test_name": "<derived>",
            "target_opcode": "<derived>",
            "model_coef_name": "<derived>",
            "selected_test": "<derived>",
            "selected_opcode": "<derived>",
            "selected_model_coef_name": "<derived>",
            "glue_adjustment": 0.0,
            "new_gas_decimal": float(value),
            "new_gas_rounded": int(rounded),
            "poor_fit": False,
        }
        for col in model_by_cols:
            all_row[col] = None
        all_row_aligned = {c: all_row.get(c) for c in new_gas_all_df.columns}
        new_gas_all_df = pd.concat(
            [new_gas_all_df, pd.DataFrame([all_row_aligned])], ignore_index=True
        )
        derived_rows.append(derived_summary)

    derived_rows_df = pd.DataFrame(derived_rows) if derived_rows else pd.DataFrame()

    # Capture raw and patched baselines for diff/sentinel rendering.
    raw_fork_baseline = dict(get_gas_costs(config.gas_costs.fork).values)
    current_values = dict(config.gas_costs_obj.values) if config.gas_costs_obj else {}

    # Final sort + reset for determinism (anchor_rate referenced upstream).
    new_gas_all_df = new_gas_all_df.sort_values(
        ["gas_param", "client_name", "test_name", "target_opcode", "model_coef_name"],
        kind="mergesort",
    ).reset_index(drop=True)
    new_gas_df = new_gas_df.sort_values("gas_param", kind="mergesort").reset_index(
        drop=True
    )
    _ = anchor_rate  # touched in aggregate; kept here for clarity in the trace.
    _ = np  # quiet linters; numpy imported for future use.

    return ProposalOutput(
        new_gas_all_df=new_gas_all_df,
        new_gas_df=new_gas_df,
        derived_rows=derived_rows_df,
        raw_fork_baseline=raw_fork_baseline,
        current_values=current_values,
        warnings=warnings_list,
        missing_glue_pairs=missing_glue_pairs,
        glue_opcodes_by_test_df=glue_opcodes_by_test_df,
    )
