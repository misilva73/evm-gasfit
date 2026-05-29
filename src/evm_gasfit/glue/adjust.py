"""Apply the glue adjustment to each fitted target coefficient.

For every ``(test_name, target_opcode, *model_by, client)`` row in
``results_df``, subtract the contribution of every priced glue opcode that
correlates with the target on that test group: ratio × glue_runtime_ms. A
glue opcode's contribution is included only when its per-client fit passed
both quality gates — ``p_value < p_threshold`` and ``rsquared >= r2_threshold``
— so a noisy glue fit cannot pull the target coefficient down on the
strength of a slope it never measured reliably. Negative adjusted
coefficients are clipped to zero, and the CI bounds are shifted by the
same amount and clipped identically.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

_log = logging.getLogger("evm_gasfit.glue")


def _model_by_cols(
    results_df: pd.DataFrame, glue_opcodes_by_test_df: pd.DataFrame
) -> list[str]:
    reserved = {
        "test_name",
        "client_name",
        "target_opcode",
        "glue_opcode",
        "corr",
        "ratio",
    }
    candidates = [c for c in glue_opcodes_by_test_df.columns if c not in reserved]
    return [c for c in candidates if c in results_df.columns]


def compute_glue_adjustment(
    results_df: pd.DataFrame,
    glue_results_df: pd.DataFrame,
    glue_opcodes_by_test_df: pd.DataFrame,
    p_threshold: float,
    r2_threshold: float,
) -> pd.DataFrame:
    """Compute per-row glue adjustment plus the clipped target coefficient.

    Returns a DataFrame keyed by ``(test_name, target_opcode, *model_by,
    client_name)`` with columns ``glue_adjustment``,
    ``adjusted_target_coef_runtime_ms``, ``adjusted_target_coef_conf_int_low``,
    and ``adjusted_target_coef_conf_int_high``.
    """
    model_by_cols = _model_by_cols(results_df, glue_opcodes_by_test_df)
    key_cols = ["test_name", "target_opcode", *model_by_cols, "client_name"]

    rows: list[dict[str, object]] = []
    for _, row in results_df.iterrows():
        ratio_mask = (glue_opcodes_by_test_df["test_name"] == row["test_name"]) & (
            glue_opcodes_by_test_df["target_opcode"] == row["target_opcode"]
        )
        for mb in model_by_cols:
            ratio_mask &= glue_opcodes_by_test_df[mb] == row[mb]
        candidates = glue_opcodes_by_test_df[ratio_mask]

        adjustment = 0.0
        if not candidates.empty and not glue_results_df.empty:
            glue_for_client = glue_results_df[
                glue_results_df["client_name"] == row["client_name"]
            ]
            for _, cand in candidates.iterrows():
                glue_row_mask = glue_for_client["glue_opcode"] == cand["glue_opcode"]
                glue_row = glue_for_client[glue_row_mask]
                if glue_row.empty:
                    continue
                pval = float(glue_row.iloc[0]["p_value"])
                r2 = float(glue_row.iloc[0]["rsquared"])
                glue_ms = float(glue_row.iloc[0]["glue_runtime_ms"])
                if (
                    np.isnan(pval)
                    or np.isnan(r2)
                    or np.isnan(glue_ms)
                    or pval >= p_threshold
                    or r2 < r2_threshold
                ):
                    continue
                adjustment += float(cand["ratio"]) * glue_ms

        target = float(row["target_coef_runtime_ms"])
        low = float(row["target_coef_conf_int_low"])
        high = float(row["target_coef_conf_int_high"])
        adjusted_target = max(0.0, target - adjustment)
        adjusted_low = max(0.0, low - adjustment)
        adjusted_high = max(0.0, high - adjustment)

        out_row: dict[str, object] = {
            "test_name": row["test_name"],
            "target_opcode": row["target_opcode"],
            "client_name": row["client_name"],
        }
        for mb in model_by_cols:
            out_row[mb] = row[mb]
        out_row.update(
            {
                "glue_adjustment": adjustment,
                "adjusted_target_coef_runtime_ms": adjusted_target,
                "adjusted_target_coef_conf_int_low": adjusted_low,
                "adjusted_target_coef_conf_int_high": adjusted_high,
            }
        )
        rows.append(out_row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    sort_cols = [c for c in key_cols if c in out.columns]
    return out.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
