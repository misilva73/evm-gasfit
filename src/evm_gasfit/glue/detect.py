"""Per-group ratio/correlation detection of glue opcodes.

Groups fixtures by ``(test_name, target_opcode, *model_by)`` (no client axis —
opcode counts are a property of the fixture). For each non-target column the
detector computes the Pearson correlation against ``opcount`` and the mean
delta ratio; opcodes passing both thresholds are returned. The result drives
``glue_opcodes_by_test.csv`` and the missing-glue warning.
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import pandas as pd

from evm_gasfit.config import ModelSpec
from evm_gasfit.modeling.estimate import (
    _apply_filters,
    _materialize_derived,
    _resolve_target_opcode,
)

from .required import PRICED_GLUE_OPCODES

_log = logging.getLogger("evm_gasfit.glue")

_MIN_BLOCK_LIMIT_POINTS = 5
_RATIO_FLOOR = 5e-4
# Columns on ``fixtures_df`` that are not per-opcode counts.
_NON_OPCODE_COLUMNS: frozenset[str] = frozenset(
    {
        "client_name",
        "fixture_name",
        "test_file",
        "test_name",
        "test_runtime_ms",
        "block_limit_million",
        "target_opcode",
        "opcount",
    }
)


def _spec_groups(
    fixtures_df: pd.DataFrame, spec: ModelSpec
) -> list[tuple[dict[str, object], pd.DataFrame]]:
    """Yield ``(group_values, group_df)`` per spec slice; empty when filters drop everything."""
    slice_df = fixtures_df[fixtures_df["test_name"] == spec.test_name]
    slice_df = _apply_filters(slice_df, spec.filter_by)
    if slice_df.empty:
        return []
    slice_df = _resolve_target_opcode(slice_df, spec)
    slice_df = _materialize_derived(slice_df, spec)

    for col in spec.model_by:
        if col not in slice_df.columns:
            return []

    out: list[tuple[dict[str, object], pd.DataFrame]] = []
    if spec.model_by:
        for key, group_df in slice_df.groupby(spec.model_by, dropna=False, sort=True):
            key_tuple = key if isinstance(key, tuple) else (key,)
            group_values = {col: val for col, val in zip(spec.model_by, key_tuple)}
            out.append((group_values, group_df))
    else:
        out.append(({}, slice_df))
    return out


def _opcode_columns(group_df: pd.DataFrame, target_opcode: str) -> list[str]:
    return [
        c
        for c in group_df.columns
        if c not in _NON_OPCODE_COLUMNS
        and c != target_opcode
        and pd.api.types.is_numeric_dtype(group_df[c])
    ]


def _passes_thresholds(
    counts: np.ndarray,
    opcount: np.ndarray,
    eps: float,
) -> tuple[bool, float, float]:
    if np.std(counts) == 0 or np.std(opcount) == 0:
        return False, float("nan"), float("nan")
    corr = float(np.corrcoef(counts, opcount)[0, 1])
    d_count = np.diff(counts)
    d_opcount = np.diff(opcount)
    if d_opcount.mean() == 0:
        return False, corr, float("nan")
    ratio = float(d_count.mean() / d_opcount.mean())
    keep = corr >= (1 - eps) and ratio >= _RATIO_FLOOR
    return keep, corr, ratio


def compute_glue_opcodes_by_test(
    fixtures_df: pd.DataFrame,
    model_specs: Iterable[ModelSpec],
    eps: float,
) -> pd.DataFrame:
    """Compute the per-test glue opcode ratio table.

    Args:
        fixtures_df: Shared fixtures frame.
        model_specs: Iterable of validated ``ModelSpec`` objects.
        eps: ``ratio_corr_eps`` from config; keep opcodes with ``corr >= 1 - eps``.

    Returns:
        DataFrame with columns ``test_name``, ``target_opcode``, every
        ``model_by`` column observed across specs, ``glue_opcode``, ``corr``,
        ``ratio``.
    """
    model_by_cols: list[str] = sorted(
        {c for spec in model_specs for c in spec.model_by}
    )
    rows: list[dict[str, object]] = []

    for spec in model_specs:
        for group_values, group_df in _spec_groups(fixtures_df, spec):
            agg = (
                group_df.groupby("block_limit_million", sort=True)
                .agg("mean", numeric_only=True)
                .reset_index()
            )
            if len(agg) < _MIN_BLOCK_LIMIT_POINTS:
                continue
            target_opcode = group_df["target_opcode"].iloc[0]
            opcount = agg["opcount"].astype(float).to_numpy()
            for col in _opcode_columns(agg, target_opcode):
                counts = agg[col].astype(float).to_numpy()
                keep, corr, ratio = _passes_thresholds(counts, opcount, eps)
                if not keep:
                    continue
                row: dict[str, object] = {
                    "test_name": spec.test_name,
                    "target_opcode": target_opcode,
                }
                for mb in model_by_cols:
                    row[mb] = group_values.get(mb)
                row["glue_opcode"] = col
                row["corr"] = corr
                row["ratio"] = ratio
                rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        cols = [
            "test_name",
            "target_opcode",
            *model_by_cols,
            "glue_opcode",
            "corr",
            "ratio",
        ]
        return pd.DataFrame(columns=cols)
    sort_cols = ["test_name", "target_opcode", *model_by_cols, "glue_opcode"]
    sort_cols = [c for c in sort_cols if c in df.columns]
    return df.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)


def detect_missing_glue(
    fixtures_df: pd.DataFrame,
    model_specs: Iterable[ModelSpec],
    eps: float,
) -> list[tuple[str, str]]:
    """Return sorted ``(test_name, glue_opcode)`` pairs that meet the thresholds but aren't priced."""
    glue_df = compute_glue_opcodes_by_test(fixtures_df, model_specs, eps)
    if glue_df.empty:
        return []
    priced = set(PRICED_GLUE_OPCODES)
    pairs = {
        (str(r["test_name"]), str(r["glue_opcode"]))
        for _, r in glue_df.iterrows()
        if r["glue_opcode"] not in priced
    }
    return sorted(pairs)
