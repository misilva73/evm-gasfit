"""Unit tests pinning the fit-failure-mode contract for the NNLS regressor.

These exercise ``modeling.estimate.estimate_models`` (which logs the WARNINGs)
and ``modeling.nnls.fit_nnls`` (where the bootstrap loop tolerates iteration
failures). The synthesized inputs go straight to the modeling layer — no YAML,
no runtime/opcount loaders — because each test is about one failure branch.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import nnls as _real_nnls

from evm_gasfit.config import Config
from evm_gasfit.errors import ModelingError
from evm_gasfit.modeling import nnls as nnls_module
from evm_gasfit.modeling.estimate import estimate_models
from evm_gasfit.modeling.nnls import fit_nnls

_LOGGER_NAME = "evm_gasfit"
_TEST_NAME = "test_arithmetic"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_config(
    *,
    model_params: dict[str, str] | None = None,
    new_params: dict[str, int | None] | None = None,
    bootstrap_iterations: int = 25,
) -> Config:
    """Build a minimal validated Config targeting a single ADD spec."""
    cfg: dict[str, Any] = {
        "version": 1,
        "anchor_rate": 1.0e8,
        "clients": ["geth"],
        "gas_costs": {"fork": "osaka"},
        "modeling": {"bootstrap_iterations": bootstrap_iterations, "random_seed": 7},
        "output": {"plots": False},
        "models": {
            "presets": [],
            "custom": [
                {
                    "test_name": _TEST_NAME,
                    "target_operation": "ADD",
                    "model_params": model_params or {"target_coef": "OPCODE_ADD"},
                }
            ],
        },
    }
    if new_params is not None:
        cfg["new_params"] = dict(new_params)
    return Config.model_validate(cfg)


def _make_fixtures_df(
    *,
    opcounts: list[float],
    runtimes: list[float] | None = None,
    extra_cols: dict[str, list[float]] | None = None,
    client: str = "geth",
) -> pd.DataFrame:
    """Build a fixtures_df slice for ``test_arithmetic`` / target ADD.

    Each row gets a unique ``fixture_name``; ``ADD`` is filled to match
    ``opcount`` so the invariant in ``_enforce_opcount_invariant`` passes.
    """
    n = len(opcounts)
    runtimes = (
        runtimes if runtimes is not None else [100.0 + 1e-5 * c for c in opcounts]
    )
    if len(runtimes) != n:
        raise ValueError("runtimes and opcounts must be the same length")
    df = pd.DataFrame(
        {
            "client_name": [client] * n,
            "fixture_name": [f"f{i}" for i in range(n)],
            "test_file": [_TEST_NAME] * n,
            "test_name": [_TEST_NAME] * n,
            "test_runtime_ms": runtimes,
            "opcount": [float(c) for c in opcounts],
            # Per-opcode count column; the invariant matches opcount to ADD.
            "ADD": [float(c) for c in opcounts],
        }
    )
    if extra_cols:
        for name, values in extra_cols.items():
            if len(values) != n:
                raise ValueError(f"extra_cols[{name!r}] length mismatch")
            df[name] = [float(v) for v in values]
    return df


# ---------------------------------------------------------------------------
# §4.2 fit-failure tests driven through estimate_models
# ---------------------------------------------------------------------------


def test_skip_when_nobs_below_features_plus_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # n_features+1 = 2 (intercept + opcount); need at least 3 rows. Give 2.
    config = _make_config()
    fixtures_df = _make_fixtures_df(opcounts=[10.0, 20.0])

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    with pytest.raises(ModelingError):
        estimate_models(config, fixtures_df)

    skip_records = [r for r in caplog.records if "skipping" in r.getMessage()]
    assert skip_records, "expected a WARNING naming the skipped fit"
    msg = skip_records[0].getMessage()
    assert _TEST_NAME in msg
    assert "geth" in msg
    assert "nobs" in msg


def test_skip_when_design_is_rank_deficient(caplog: pytest.LogCaptureFixture) -> None:
    # Two extras that are equal row-by-row produce perfectly collinear
    # opcount*param columns in the design matrix.
    config = _make_config(
        model_params={
            "target_coef": "OPCODE_ADD",
            "feat_a": "OPCODE_SUB",
            "feat_b": "OPCODE_MUL",
        },
    )
    n = 8
    fixtures_df = _make_fixtures_df(
        opcounts=[10.0 * (i + 1) for i in range(n)],
        extra_cols={
            "feat_a": [1.0 + (i % 3) for i in range(n)],
            "feat_b": [1.0 + (i % 3) for i in range(n)],
        },
    )

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    with pytest.raises(ModelingError):
        estimate_models(config, fixtures_df)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "skipping" in m and "rank" in m.lower() and _TEST_NAME in m and "geth" in m
        for m in msgs
    ), f"expected a rank-deficient skip warning; got: {msgs}"


@pytest.mark.parametrize(
    ("opcounts", "label"),
    [
        ([5.0, 5.0, 5.0, 5.0], "constant"),
        ([0.0, 0.0, 0.0, 0.0], "zero"),
    ],
)
def test_skip_when_opcount_is_constant_or_zero(
    opcounts: list[float],
    label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The opcount=0 path also trips the fixtures_df invariant (target opcount
    # must be > 0). Both kill target_coef identifiability and must be skipped.
    config = _make_config()
    fixtures_df = _make_fixtures_df(
        opcounts=opcounts,
        runtimes=[100.0, 101.0, 99.0, 100.5],
    )

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    if label == "zero":
        # _enforce_opcount_invariant rejects opcount=0 outright as a ConfigError
        # before the fit step is reached — exercise that path instead.
        from evm_gasfit.errors import ConfigError

        with pytest.raises(ConfigError, match="opcount=0"):
            estimate_models(config, fixtures_df)
        return

    with pytest.raises(ModelingError):
        estimate_models(config, fixtures_df)

    skip_msgs = [r.getMessage() for r in caplog.records if "skipping" in r.getMessage()]
    assert any(
        "constant" in m and _TEST_NAME in m and "geth" in m for m in skip_msgs
    ), f"expected constant-opcount skip; got: {skip_msgs}"


def test_skip_when_scipy_nnls_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Force the solver to raise on every call: scipy raises RuntimeError on
    # non-convergence in production, so use that real exception type.
    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("nnls forced failure")

    monkeypatch.setattr(nnls_module, "nnls", boom)

    config = _make_config()
    fixtures_df = _make_fixtures_df(opcounts=[10.0, 20.0, 30.0, 40.0, 50.0])

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    with pytest.raises(ModelingError):
        estimate_models(config, fixtures_df)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "NNLS solver raised" in m and _TEST_NAME in m and "geth" in m for m in msgs
    ), f"expected scipy-raise skip warning; got: {msgs}"


def test_modeling_error_when_every_fit_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # One client, one spec, one group, too-few-rows → the lone fit is skipped
    # and the whole run produces zero result rows.
    config = _make_config()
    fixtures_df = _make_fixtures_df(opcounts=[10.0, 20.0])

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    with pytest.raises(ModelingError, match="every model spec was skipped"):
        estimate_models(config, fixtures_df)


# ---------------------------------------------------------------------------
# §4.2 bootstrap-iteration failure — driven through fit_nnls directly
# ---------------------------------------------------------------------------


def test_bootstrap_iteration_failures_dont_break_primary_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Primary fit must succeed; bootstrap iterations after the first should
    # raise. NNLSResults filters NaN rows out of inference and surfaces the
    # reduced success count on its summary string.
    rng = np.random.default_rng(0)
    opcounts = np.linspace(10.0, 100.0, 20)
    runtimes = 5.0 + 0.5 * opcounts + rng.normal(0.0, 0.1, size=opcounts.size)
    df = pd.DataFrame({"opcount": opcounts, "test_runtime_ms": runtimes})

    call_counter = {"n": 0}

    def flaky_nnls(A: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
        call_counter["n"] += 1
        # First call is the primary fit; let it succeed. Every other call (the
        # bootstrap iterations) raises so they end up as NaN rows.
        if call_counter["n"] == 1:
            return _real_nnls(A, b)
        raise RuntimeError("bootstrap iteration forced failure")

    monkeypatch.setattr(nnls_module, "nnls", flaky_nnls)

    n_bootstrap = 8
    result = fit_nnls(
        df,
        features=["opcount"],
        target="test_runtime_ms",
        n_bootstrap=n_bootstrap,
        random_seed=1,
    )

    # Primary fit completed despite every bootstrap iteration failing.
    assert result.nobs == len(opcounts)
    assert float(result.params["opcount"]) > 0
    # All bootstrap iterations failed → success counter is zero, p-values fall
    # back to 1.0 (unidentifiable), and confidence intervals are NaN.
    assert result._n_bootstrap_total == n_bootstrap
    assert result._n_bootstrap_success == 0
    assert (result.pvalues == 1.0).all()
    ci = result.conf_int()
    assert ci.isna().all().all()
    # The summary string surfaces the iteration tally per the §4.2 note.
    summary = result.summary()
    assert f"0 of {n_bootstrap} iterations succeeded" in summary


def test_bootstrap_partial_failures_reduce_n_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mixed-success case: half the bootstrap iterations raise. Inference still
    # runs against the surviving draws; std errors are finite.
    rng = np.random.default_rng(0)
    opcounts = np.linspace(10.0, 100.0, 20)
    runtimes = 5.0 + 0.5 * opcounts + rng.normal(0.0, 0.1, size=opcounts.size)
    df = pd.DataFrame({"opcount": opcounts, "test_runtime_ms": runtimes})

    call_counter = {"n": 0}

    def flaky_nnls(A: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
        call_counter["n"] += 1
        # First call (primary) succeeds; even-numbered subsequent calls raise.
        if call_counter["n"] == 1 or call_counter["n"] % 2 == 1:
            return _real_nnls(A, b)
        raise RuntimeError("forced odd-iteration failure")

    monkeypatch.setattr(nnls_module, "nnls", flaky_nnls)

    n_bootstrap = 10
    result = fit_nnls(
        df,
        features=["opcount"],
        target="test_runtime_ms",
        n_bootstrap=n_bootstrap,
        random_seed=1,
    )

    assert 0 < result._n_bootstrap_success < n_bootstrap
    summary = result.summary()
    assert (
        f"{result._n_bootstrap_success} of {n_bootstrap} iterations succeeded"
        in summary
    )
