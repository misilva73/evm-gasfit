"""Two-tier glue-opcode regression.

Pure-glue opcodes are fit one at a time as single-feature NNLS per
``(client, opcode)``. Cycle-glue opcodes are fit jointly per client: a single
NNLS over the union of their driver fixtures with all seven cycle features
present at once.
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
    CYCLE_GLUE_OPCODES,
    PRICED_GLUE_OPCODES,
    PURE_GLUE_OPCODES,
    validate_inputs,
)

_log = logging.getLogger("evm_gasfit.glue")


@dataclass
class GlueEstimateOutput:
    """Glue results frame plus the per-(client, opcode) fits."""

    results_df: pd.DataFrame
    fits: dict[tuple[str, str], NNLSResults] = field(default_factory=dict)


def _pure_fit(
    fixtures_df: pd.DataFrame,
    config: Config,
    client: str,
    opcode: str,
) -> NNLSResults | None:
    slice_df = fixtures_df[
        (fixtures_df["client_name"] == client)
        & (fixtures_df["test_name"] == opcode)
    ]
    if slice_df.empty or opcode not in slice_df.columns:
        _log.warning(
            "glue pure-fit skipped: client=%s opcode=%s has no driver fixtures",
            client,
            opcode,
        )
        return None
    counts = slice_df[opcode].astype(float).to_numpy()
    if len(set(counts.tolist())) <= 1 or np.all(counts == 0):
        _log.warning(
            "glue pure-fit skipped: client=%s opcode=%s count is constant or zero",
            client,
            opcode,
        )
        return None
    design = pd.DataFrame(
        {
            opcode: counts,
            "test_runtime_ms": slice_df["test_runtime_ms"].astype(float).to_numpy(),
        }
    )
    try:
        return fit_nnls(
            design,
            features=[opcode],
            target="test_runtime_ms",
            n_bootstrap=config.modeling.bootstrap_iterations,
            random_seed=config.modeling.random_seed,
        )
    except Exception as exc:
        _log.warning(
            "glue pure-fit failed: client=%s opcode=%s exc=%s", client, opcode, exc
        )
        return None


def _cycle_fit(
    fixtures_df: pd.DataFrame,
    config: Config,
    client: str,
) -> NNLSResults | None:
    cycle_tests = set(CYCLE_GLUE_OPCODES)
    slice_df = fixtures_df[
        (fixtures_df["client_name"] == client)
        & (fixtures_df["test_name"].isin(cycle_tests))
    ]
    missing = [op for op in CYCLE_GLUE_OPCODES if op not in slice_df.columns]
    if slice_df.empty or missing:
        _log.warning(
            "glue cycle-fit skipped: client=%s missing columns=%r",
            client,
            missing,
        )
        return None
    design = slice_df[CYCLE_GLUE_OPCODES + ["test_runtime_ms"]].astype(float).copy()
    try:
        return fit_nnls(
            design,
            features=list(CYCLE_GLUE_OPCODES),
            target="test_runtime_ms",
            n_bootstrap=config.modeling.bootstrap_iterations,
            random_seed=config.modeling.random_seed,
        )
    except Exception as exc:
        _log.warning("glue cycle-fit failed: client=%s exc=%s", client, exc)
        return None


def _row(client: str, opcode: str, fit: NNLSResults | None) -> dict[str, object]:
    if fit is None:
        return {
            "client_name": client,
            "glue_opcode": opcode,
            "nobs": 0,
            "glue_runtime_ms": float("nan"),
            "p_value": float("nan"),
            "rsquared": float("nan"),
        }
    return {
        "client_name": client,
        "glue_opcode": opcode,
        "nobs": int(fit.nobs),
        "glue_runtime_ms": float(fit.params[opcode]),
        "p_value": float(fit.pvalues[opcode]),
        "rsquared": float(fit.rsquared),
    }


def estimate_glue(config: Config, fixtures_df: pd.DataFrame) -> GlueEstimateOutput:
    """Fit one NNLS per (client, glue_opcode).

    Pure-glue opcodes get a single-feature regression each; cycle-glue opcodes
    share a joint per-client fit. Raises ``ConfigError`` if any required driver
    test is absent from ``fixtures_df``.
    """
    validate_inputs(fixtures_df)
    rows: list[dict[str, object]] = []
    fits: dict[tuple[str, str], NNLSResults] = {}

    clients = sorted(fixtures_df["client_name"].unique())
    for client in clients:
        for opcode in PURE_GLUE_OPCODES:
            fit = _pure_fit(fixtures_df, config, client, opcode)
            rows.append(_row(client, opcode, fit))
            if fit is not None:
                fits[(client, opcode)] = fit

        cycle_fit = _cycle_fit(fixtures_df, config, client)
        for opcode in CYCLE_GLUE_OPCODES:
            if cycle_fit is None:
                rows.append(_row(client, opcode, None))
                continue
            rows.append(
                {
                    "client_name": client,
                    "glue_opcode": opcode,
                    "nobs": int(cycle_fit.nobs),
                    "glue_runtime_ms": float(cycle_fit.params[opcode]),
                    "p_value": float(cycle_fit.pvalues[opcode]),
                    "rsquared": float(cycle_fit.rsquared),
                }
            )
            fits[(client, opcode)] = cycle_fit

    results_df = pd.DataFrame(rows)
    opcode_order = {op: i for i, op in enumerate(PRICED_GLUE_OPCODES)}
    results_df["_order"] = results_df["glue_opcode"].map(opcode_order)
    results_df = (
        results_df.sort_values(["client_name", "_order"], kind="mergesort")
        .drop(columns="_order")
        .reset_index(drop=True)
    )
    return GlueEstimateOutput(results_df=results_df, fits=fits)
