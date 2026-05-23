"""Two-tier glue-opcode regression.

Pure-glue opcodes are fit one at a time as single-feature NNLS per
``(client, spec)``. Cycle-glue opcodes are fit jointly per client: a single
NNLS over the union of their driver fixtures with one feature per cycle
spec, where each feature is the row-wise sum of that spec's family
members (so DUP1..DUP16 collapse into one ``DUP`` feature).

Specs without a driver fixture (``spec.test_name is None``) are skipped
silently; ``validate_inputs`` already emitted the warning at load time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from evm_gasfit.config import Config
from evm_gasfit.modeling.nnls import fit_nnls
from evm_gasfit.modeling.results import NNLSResults

from .required import (
    PRICED_GLUE_SPECS,
    GlueOpcodeSpec,
    validate_inputs,
)

_log = logging.getLogger("evm_gasfit.glue")


@dataclass
class GlueEstimateOutput:
    """Glue results frame plus the per-(client, canonical-name) fits."""

    results_df: pd.DataFrame
    fits: dict[tuple[str, str], NNLSResults] = field(default_factory=dict)


def _spec_member_filter(spec: GlueOpcodeSpec) -> set[str] | None:
    """Return the ``opcode``-param values driving this spec, or ``None`` if no filter needed."""
    if spec.test_opcode_filter is not None:
        return {spec.test_opcode_filter}
    if len(spec.members) > 1:
        return set(spec.members)
    return None


def _slice_for_spec(
    fixtures_df: pd.DataFrame, client: str, spec: GlueOpcodeSpec
) -> pd.DataFrame:
    slice_df = fixtures_df[
        (fixtures_df["client_name"] == client)
        & (fixtures_df["test_name"] == spec.test_name)
    ]
    member_filter = _spec_member_filter(spec)
    if member_filter is not None and "opcode" in slice_df.columns:
        slice_df = slice_df[slice_df["opcode"].isin(member_filter)]
    return slice_df


def _canonical_count(slice_df: pd.DataFrame, spec: GlueOpcodeSpec) -> np.ndarray:
    cols = [m for m in spec.members if m in slice_df.columns]
    if not cols:
        return np.zeros(len(slice_df), dtype=float)
    return slice_df[cols].astype(float).sum(axis=1).to_numpy()


def _pure_fit(
    fixtures_df: pd.DataFrame,
    config: Config,
    client: str,
    spec: GlueOpcodeSpec,
) -> NNLSResults | None:
    slice_df = _slice_for_spec(fixtures_df, client, spec)
    if slice_df.empty:
        _log.warning(
            "glue pure-fit skipped: client=%s opcode=%s has no driver fixtures",
            client,
            spec.name,
        )
        return None
    counts = _canonical_count(slice_df, spec)
    if len(set(counts.tolist())) <= 1 or np.all(counts == 0):
        _log.warning(
            "glue pure-fit skipped: client=%s opcode=%s count is constant or zero",
            client,
            spec.name,
        )
        return None
    design = pd.DataFrame(
        {
            spec.name: counts,
            "test_runtime_ms": slice_df["test_runtime_ms"].astype(float).to_numpy(),
        }
    )
    try:
        return fit_nnls(
            design,
            features=[spec.name],
            target="test_runtime_ms",
            n_bootstrap=config.modeling.bootstrap_iterations,
            random_seed=config.modeling.random_seed,
        )
    except Exception as exc:
        _log.warning(
            "glue pure-fit failed: client=%s opcode=%s exc=%s",
            client,
            spec.name,
            exc,
        )
        return None


def _cycle_fit(
    fixtures_df: pd.DataFrame,
    config: Config,
    client: str,
    cycle_specs: list[GlueOpcodeSpec],
) -> NNLSResults | None:
    slices: list[pd.DataFrame] = []
    for spec in cycle_specs:
        if spec.test_name is None:
            continue
        slc = _slice_for_spec(fixtures_df, client, spec)
        if not slc.empty:
            slices.append(slc)
    if not slices:
        _log.warning("glue cycle-fit skipped: client=%s no driver rows", client)
        return None
    combined = pd.concat(slices, ignore_index=True)

    feature_names = [spec.name for spec in cycle_specs]
    design_cols: dict[str, np.ndarray] = {
        spec.name: _canonical_count(combined, spec) for spec in cycle_specs
    }
    design_cols["test_runtime_ms"] = (
        combined["test_runtime_ms"].astype(float).to_numpy()
    )
    design = pd.DataFrame(design_cols)
    try:
        return fit_nnls(
            design,
            features=feature_names,
            target="test_runtime_ms",
            n_bootstrap=config.modeling.bootstrap_iterations,
            random_seed=config.modeling.random_seed,
        )
    except Exception as exc:
        _log.warning("glue cycle-fit failed: client=%s exc=%s", client, exc)
        return None


def _row(client: str, name: str, fit: NNLSResults | None) -> dict[str, object]:
    if fit is None:
        return {
            "client_name": client,
            "glue_opcode": name,
            "nobs": 0,
            "glue_runtime_ms": float("nan"),
            "p_value": float("nan"),
            "rsquared": float("nan"),
        }
    return {
        "client_name": client,
        "glue_opcode": name,
        "nobs": int(fit.nobs),
        "glue_runtime_ms": float(fit.params[name]),
        "p_value": float(fit.pvalues[name]),
        "rsquared": float(fit.rsquared),
    }


def estimate_glue(config: Config, fixtures_df: pd.DataFrame) -> GlueEstimateOutput:
    """Fit one NNLS per (client, canonical glue name).

    Pure-glue specs get a single-feature regression each; cycle-glue specs
    share a joint per-client fit. Specs whose ``test_name is None`` are
    skipped (no row emitted). Raises ``ConfigError`` if any required driver
    test is absent from ``fixtures_df``.
    """
    validate_inputs(fixtures_df)
    rows: list[dict[str, object]] = []
    fits: dict[tuple[str, str], NNLSResults] = {}

    active_pure = [
        s for s in PRICED_GLUE_SPECS if s.tier == "pure" and s.test_name is not None
    ]
    active_cycle = [
        s for s in PRICED_GLUE_SPECS if s.tier == "cycle" and s.test_name is not None
    ]

    clients = sorted(fixtures_df["client_name"].unique())
    for client in clients:
        for spec in active_pure:
            fit = _pure_fit(fixtures_df, config, client, spec)
            rows.append(_row(client, spec.name, fit))
            if fit is not None:
                fits[(client, spec.name)] = fit

        cycle_fit = _cycle_fit(fixtures_df, config, client, active_cycle)
        for spec in active_cycle:
            if cycle_fit is None:
                rows.append(_row(client, spec.name, None))
                continue
            rows.append(_row(client, spec.name, cycle_fit))
            fits[(client, spec.name)] = cycle_fit

    results_df = pd.DataFrame(rows)
    active_names = [s.name for s in active_pure] + [s.name for s in active_cycle]
    opcode_order = {name: i for i, name in enumerate(active_names)}
    results_df["_order"] = results_df["glue_opcode"].map(opcode_order)
    results_df = (
        results_df.sort_values(["client_name", "_order"], kind="mergesort")
        .drop(columns="_order")
        .reset_index(drop=True)
    )
    return GlueEstimateOutput(results_df=results_df, fits=fits)
