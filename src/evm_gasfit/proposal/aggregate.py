"""Per-client and across-client aggregation of fitted runtimes into gas params.

Expands each ``results.csv`` row into one row per ``model_params`` entry
(target_coef + extras), applies glue adjustment to the target row's runtime,
selects per-client and across-client worst-case values, and surfaces the
``poor_fit`` flag.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from evm_gasfit.config import Config


def _all_model_by_cols(config: Config) -> list[str]:
    return sorted({c for spec in config.resolved_models for c in spec.model_by})


def _lookup_glue_adjustment(
    glue_adjustment_df: pd.DataFrame | None,
    test_name: str,
    target_opcode: str,
    model_by: list[str],
    model_by_values: dict[str, object],
    client: str,
) -> tuple[float, float | None, float | None, float | None]:
    """Return (adjustment, adjusted_runtime, adjusted_low, adjusted_high) or zeros."""
    if glue_adjustment_df is None or glue_adjustment_df.empty:
        return 0.0, None, None, None
    mask = (
        (glue_adjustment_df["test_name"] == test_name)
        & (glue_adjustment_df["target_opcode"] == target_opcode)
        & (glue_adjustment_df["client_name"] == client)
    )
    for col in model_by:
        if col in glue_adjustment_df.columns:
            mask &= glue_adjustment_df[col] == model_by_values[col]
    sub = glue_adjustment_df[mask]
    if sub.empty:
        return 0.0, None, None, None
    row = sub.iloc[0]
    return (
        float(row["glue_adjustment"]),
        float(row["adjusted_target_coef_runtime_ms"]),
        float(row["adjusted_target_coef_conf_int_low"]),
        float(row["adjusted_target_coef_conf_int_high"]),
    )


def expand_to_per_client(
    results_df: pd.DataFrame,
    config: Config,
    glue_adjustment_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """Expand one results_df row into N rows per ``model_params`` entry."""
    model_by_cols = _all_model_by_cols(config)
    anchor_rate = float(config.anchor_rate)

    rows: list[dict[str, object]] = []
    for spec in config.resolved_models:
        for _, res_row in results_df.iterrows():
            if res_row["test_name"] != spec.test_name:
                continue
            # Match the spec's exact model_by combo on this row.
            spec_match = True
            for col in model_by_cols:
                in_spec = col in spec.model_by
                val = res_row.get(col) if col in res_row.index else None
                is_present = not (
                    val is None or (isinstance(val, float) and np.isnan(val))
                )
                if in_spec and not is_present:
                    spec_match = False
                    break
                if not in_spec and is_present:
                    spec_match = False
                    break
            if not spec_match:
                continue
            # Skip results rows whose gas-param target doesn't match this spec's writes.
            # Specs with the same (test_name, model_by) may differ on model_params.
            # The fit_key in estimate_models doesn't track which spec produced a row,
            # so a duplicate (test_name, target_opcode, model_by, client) means
            # both specs would expand the same row. That's acceptable per the
            # plan §2.6 "duplicate test_name across presets/custom allowed" rule.

            model_by_values = {c: res_row[c] for c in spec.model_by}
            target_opcode = res_row["target_opcode"]
            client = res_row["client_name"]

            (
                glue_adjustment,
                adj_runtime,
                adj_low,
                adj_high,
            ) = _lookup_glue_adjustment(
                glue_adjustment_df,
                spec.test_name,
                target_opcode,
                spec.model_by,
                model_by_values,
                client,
            )

            for coef_name, gas_param in spec.model_params.items():
                if coef_name == "target_coef":
                    if adj_runtime is not None:
                        runtime_ms = adj_runtime
                        ci_low = adj_low
                        ci_high = adj_high
                    else:
                        runtime_ms = float(res_row["target_coef_runtime_ms"])
                        ci_low = float(res_row["target_coef_conf_int_low"])
                        ci_high = float(res_row["target_coef_conf_int_high"])
                    pvalue = float(res_row["target_coef_pvalue"])
                    row_glue_adjustment = float(glue_adjustment)
                else:
                    rt_col = f"{coef_name}_runtime_ms"
                    if rt_col not in res_row.index:
                        continue
                    val = res_row[rt_col]
                    if val is None or (isinstance(val, float) and np.isnan(val)):
                        continue
                    runtime_ms = float(val)
                    pvalue = float(res_row[f"{coef_name}_pvalue"])
                    ci_low = float(res_row[f"{coef_name}_conf_int_low"])
                    ci_high = float(res_row[f"{coef_name}_conf_int_high"])
                    row_glue_adjustment = 0.0

                new_gas_decimal = anchor_rate * runtime_ms / 1000.0
                new_gas_rounded = math.ceil(new_gas_decimal)

                out: dict[str, object] = {
                    "gas_param": gas_param,
                    "client_name": client,
                    "runtime_ms": runtime_ms,
                    "pvalue": pvalue,
                    "conf_int_low": ci_low,
                    "conf_int_high": ci_high,
                    "test_name": spec.test_name,
                    "target_opcode": target_opcode,
                    "model_coef_name": coef_name,
                    "glue_adjustment": row_glue_adjustment,
                }
                for col in model_by_cols:
                    if col in spec.model_by:
                        out[col] = model_by_values[col]
                    else:
                        out[col] = None
                out["new_gas_decimal"] = new_gas_decimal
                out["new_gas_rounded"] = new_gas_rounded
                out["poor_fit"] = False
                rows.append(out)

    cols = (
        [
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
        ]
        + model_by_cols
        + ["new_gas_decimal", "new_gas_rounded", "poor_fit"]
    )
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows, columns=cols)
    sort_cols = [
        "gas_param",
        "client_name",
        "test_name",
        "target_opcode",
        "model_coef_name",
    ]
    sort_cols = [c for c in sort_cols if c in df.columns]
    return df.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)


def _model_by_combo(row: pd.Series, model_by_cols: list[str]) -> str:
    parts: list[str] = []
    for col in model_by_cols:
        val = row.get(col)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            continue
        parts.append(str(val))
    return "_".join(parts) if parts else ""


def select_per_client_max(
    new_gas_all_df: pd.DataFrame,
    poor_fit_threshold: float,
) -> pd.DataFrame:
    """Select the winning row per ``(gas_param, client_name)``.

    Mutates ``new_gas_all_df['poor_fit']`` so winners chosen via the fallback
    branch are flagged ``True`` on the original frame.
    """
    if new_gas_all_df.empty:
        return new_gas_all_df.copy()

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

    df = new_gas_all_df.copy()
    df["_combo"] = df.apply(lambda r: _model_by_combo(r, model_by_cols), axis=1)
    df["_idx"] = df.index

    chosen_indices: list[int] = []
    for (_gp, _client), group in df.groupby(["gas_param", "client_name"], sort=True):
        qualified = group[group["pvalue"] < poor_fit_threshold]
        if not qualified.empty:
            sub = qualified
            poor = False
        else:
            sub = group
            poor = True
        sub = sub.sort_values(
            by=[
                "runtime_ms",
                "pvalue",
                "test_name",
                "target_opcode",
                "model_coef_name",
                "_combo",
            ],
            ascending=[False, True, True, True, True, True],
            kind="mergesort",
        )
        winner_idx = int(sub.iloc[0]["_idx"])
        chosen_indices.append(winner_idx)
        if poor:
            new_gas_all_df.loc[winner_idx, "poor_fit"] = True

    chosen = new_gas_all_df.loc[chosen_indices].copy()
    chosen = chosen.sort_values(
        ["gas_param", "client_name"], kind="mergesort"
    ).reset_index(drop=True)
    return chosen


def select_across_client_max(per_client_df: pd.DataFrame) -> pd.DataFrame:
    """For each gas_param, pick the row with the largest ``runtime_ms``."""
    if per_client_df.empty:
        cols = [
            "gas_param",
            "client_name",
            "runtime_ms",
            "conf_int_low",
            "conf_int_high",
            "selected_test",
            "selected_opcode",
            "selected_model_coef_name",
            "glue_adjustment",
            "new_gas_decimal",
            "new_gas_rounded",
        ]
        return pd.DataFrame(columns=cols)

    model_by_cols = [
        c
        for c in per_client_df.columns
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

    chosen_rows: list[pd.Series] = []
    for _gp, group in per_client_df.groupby("gas_param", sort=True):
        sub = group.sort_values(
            by=["runtime_ms", "client_name"],
            ascending=[False, True],
            kind="mergesort",
        )
        chosen_rows.append(sub.iloc[0])

    df = pd.DataFrame(chosen_rows).reset_index(drop=True)
    df = df.rename(
        columns={
            "test_name": "selected_test",
            "target_opcode": "selected_opcode",
            "model_coef_name": "selected_model_coef_name",
        }
    )
    out_cols = (
        [
            "gas_param",
            "client_name",
            "runtime_ms",
            "conf_int_low",
            "conf_int_high",
            "selected_test",
            "selected_opcode",
            "selected_model_coef_name",
            "glue_adjustment",
        ]
        + model_by_cols
        + ["new_gas_decimal", "new_gas_rounded"]
    )
    out_cols = [c for c in out_cols if c in df.columns]
    df = df[out_cols]
    return df.sort_values("gas_param", kind="mergesort").reset_index(drop=True)
