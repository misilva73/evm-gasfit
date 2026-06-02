"""Per-spec NNLS estimation entry point.

Consumes the validated :class:`Config` and the shared ``fixtures_df`` built by
``io/fixtures.py``, produces the canonical ``results.csv`` DataFrame plus a
parallel dict of :class:`NNLSResults` objects keyed by fit identity so the
reports layer can render summaries and diagnostics without refitting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from evm_gasfit.config import Config, ModelSpec
from evm_gasfit.errors import ConfigError, ModelingError

from .nnls import fit_nnls
from .results import NNLSResults

_log = logging.getLogger("evm_gasfit.estimate")


@dataclass
class EstimateOutput:
    """Bundle of ``results_df`` and the parallel ``fits`` dict.

    ``fits`` is keyed by
    ``(source_label, test_name, target_opcode, *model_by_values, client_name)``
    so the reports layer can look up the underlying :class:`NNLSResults` for
    each row in ``results_df``. ``source_label`` leads the key so two specs
    sharing test_name + target + model_by (differing only in ``filter_by``)
    don't overwrite each other's fit.
    """

    results_df: pd.DataFrame
    fits: dict[tuple, NNLSResults] = field(default_factory=dict)


def _apply_filters(df: pd.DataFrame, filter_by: list[str]) -> pd.DataFrame:
    """AND-substring-match ``filter_by`` tokens against ``fixture_name``.

    A ``!``-prefixed token negates: ``!foo`` requires that ``foo`` is absent
    from the fixture name. All tokens are ANDed together.
    """
    if not filter_by:
        return df
    mask = pd.Series(True, index=df.index)
    for token in filter_by:
        if token.startswith("!"):
            mask &= ~df["fixture_name"].str.contains(token[1:], regex=False)
        else:
            mask &= df["fixture_name"].str.contains(token, regex=False)
    return df[mask]


def _materialize_derived(df: pd.DataFrame, spec: ModelSpec) -> pd.DataFrame:
    """Materialize each ``fixture_params`` entry as a float column on ``df``."""
    if not spec.fixture_params:
        return df
    df = df.copy()
    for derived_name, fp_spec in spec.fixture_params.items():
        source = fp_spec.source
        if source not in df.columns:
            raise ModelingError(
                f"spec test_name={spec.test_name!r}: fixture_params[{derived_name!r}] "
                f"source column {source!r} is missing on the filtered fixtures"
            )
        source_col = df[source].astype(str)
        if fp_spec.values is not None:
            observed = set(source_col.unique())
            unmapped = observed - set(fp_spec.values)
            if unmapped:
                raise ModelingError(
                    f"spec test_name={spec.test_name!r}: fixture_params[{derived_name!r}] "
                    f"values map omits observed source value(s) {sorted(unmapped)!r}"
                )
            df[derived_name] = source_col.map(fp_spec.values).astype(float)
        else:
            try:
                numeric = source_col.astype(float)
            except (TypeError, ValueError) as exc:
                raise ModelingError(
                    f"spec test_name={spec.test_name!r}: fixture_params[{derived_name!r}] "
                    f"source {source!r} contains non-numeric values; supply a 'values:' map"
                ) from exc
            if fp_spec.transform == "bytes_to_words":
                numeric = np.ceil(numeric.to_numpy() / 32.0).astype(float)
            df[derived_name] = numeric
    return df


def _resolve_target_opcode(df: pd.DataFrame, spec: ModelSpec) -> pd.DataFrame:
    """Fill a ``target_opcode`` column per the spec's target rule."""
    df = df.copy()
    if spec.target_operation is not None:
        df["target_opcode"] = spec.target_operation
    else:
        param = spec.target_operation_param
        if param not in df.columns:
            raise ModelingError(
                f"spec test_name={spec.test_name!r}: target_operation_param "
                f"{param!r} is missing on the filtered fixtures"
            )
        if df[param].isna().any():
            missing = df.loc[df[param].isna(), "fixture_name"].tolist()
            raise ModelingError(
                f"spec test_name={spec.test_name!r}: target_operation_param "
                f"{param!r} is null on fixtures: {missing[:5]!r}"
            )
        df["target_opcode"] = df[param].astype(str)
    return df


def _enforce_opcount_invariant(df: pd.DataFrame, spec: ModelSpec) -> None:
    """Check ``opcount == row[count_source]`` per the input invariant.

    For ordinary opcode targets the count source is the resolved target opcode
    itself. For precompile specs (``target_operation_count_source`` set), the
    target is a synthetic display name with no opcount column, so the
    invariant is checked against the override column (typically ``STATICCALL``).
    """
    count_source_override = spec.target_operation_count_source
    for _, row in df.iterrows():
        count_source = count_source_override or row["target_opcode"]
        if count_source not in df.columns:
            raise ConfigError(
                f"fixture {row['fixture_name']!r}: count source {count_source!r} "
                f"has no per-opcode count column"
            )
        expected = row["opcount"]
        actual = row[count_source]
        if pd.isna(actual) or float(expected) != float(actual):
            raise ConfigError(
                f"fixture {row['fixture_name']!r}: opcount={expected} disagrees with "
                f"per-opcode count for {count_source!r}={actual} "
                f"(spec test_name={spec.test_name!r})"
            )
        if float(expected) == 0:
            raise ConfigError(
                f"fixture {row['fixture_name']!r}: opcount=0 for target "
                f"{row['target_opcode']!r} (spec test_name={spec.test_name!r}); "
                f"fixtures that don't execute the target opcode don't belong in "
                f"this group — narrow filter_by or drop the fixture"
            )


def _build_design(
    df: pd.DataFrame,
    spec: ModelSpec,
) -> tuple[pd.DataFrame, list[str]]:
    """Build the design matrix for one (group, client) slice.

    Returns the (renamed) frame and the list of non-``target_coef`` feature
    names that survived the one-value-extras filter.
    """
    design = pd.DataFrame(
        {
            "opcount": df["opcount"].astype(float).to_numpy(),
            "test_runtime_ms": df["test_runtime_ms"].astype(float).to_numpy(),
        },
        index=df.index,
    )
    extras: list[str] = []
    for coef_name, _gas_param in spec.model_params.items():
        if coef_name == "target_coef":
            continue
        # A model_params key can reference either a derived column produced by
        # ``_materialize_derived`` (its natural name) or a raw parsed-param
        # column (exposed as ``param_<name>`` by ``build_fixtures_df``).
        if coef_name in df.columns:
            source_col = coef_name
        elif f"param_{coef_name}" in df.columns:
            source_col = f"param_{coef_name}"
        else:
            raise ModelingError(
                f"spec test_name={spec.test_name!r}: model_params coefficient "
                f"{coef_name!r} has no matching fixture-param column"
            )
        param_vals = df[source_col].astype(float).to_numpy()
        if len(set(param_vals.tolist())) <= 1:
            _log.warning(
                "spec test_name=%r: dropping extra feature %r — single unique value "
                "across the filtered fixtures",
                spec.test_name,
                coef_name,
            )
            continue
        design[coef_name] = design["opcount"].to_numpy() * param_vals
        extras.append(coef_name)
    return design, extras


def _fit_or_skip(
    design: pd.DataFrame,
    features: list[str],
    config: Config,
    spec: ModelSpec,
    client: str,
    group_label: str,
) -> NNLSResults | None:
    """Run NNLS or log + skip per the §4.2 failure modes."""
    n_features_with_const = len(features) + 1
    if len(design) < n_features_with_const + 1:
        _log.warning(
            "spec test_name=%r group=%s client=%s: nobs=%d < n_features+1=%d, skipping",
            spec.test_name,
            group_label,
            client,
            len(design),
            n_features_with_const + 1,
        )
        return None
    opcount = design["opcount"].to_numpy()
    if len(set(opcount.tolist())) <= 1 or np.all(opcount == 0):
        _log.warning(
            "spec test_name=%r group=%s client=%s: opcount is constant or zero, skipping",
            spec.test_name,
            group_label,
            client,
        )
        return None
    feature_matrix = design[features].to_numpy(dtype=float)
    design_with_const = np.column_stack([np.ones(len(feature_matrix)), feature_matrix])
    if np.linalg.matrix_rank(design_with_const) < design_with_const.shape[1]:
        _log.warning(
            "spec test_name=%r group=%s client=%s: design matrix is rank-deficient, skipping",
            spec.test_name,
            group_label,
            client,
        )
        return None
    try:
        return fit_nnls(
            design,
            features=features,
            target="test_runtime_ms",
            n_bootstrap=config.modeling.bootstrap_iterations,
            random_seed=config.modeling.random_seed,
        )
    except Exception as exc:
        _log.warning(
            "spec test_name=%r group=%s client=%s: NNLS solver raised %s, skipping",
            spec.test_name,
            group_label,
            client,
            exc,
        )
        return None


def _build_result_row(
    *,
    spec: ModelSpec,
    client: str,
    target_opcode: str,
    group_values: dict[str, str],
    fit: NNLSResults,
    extras: list[str],
) -> dict[str, object]:
    ci = fit.conf_int()
    row: dict[str, object] = {
        "test_name": spec.test_name,
        "client_name": client,
        "target_opcode": target_opcode,
        # Provenance: the exact resolved spec that produced this fit. Two specs
        # sharing test_name + target + model_by (differing only in filter_by)
        # land on identical key columns, so the aggregator routes rows back to
        # their spec by this label rather than by the key shape.
        "source_label": spec.source_label,
    }
    row.update(group_values)
    row.update(
        {
            "nobs": fit.nobs,
            "intercept_runtime_ms": float(fit.params["const"]),
            "intercept_pvalue": float(fit.pvalues["const"]),
            "rsquared": float(fit.rsquared),
            "rsquared_adj": float(fit.rsquared_adj),
            "target_coef_runtime_ms": float(fit.params["opcount"]),
            "target_coef_pvalue": float(fit.pvalues["opcount"]),
            "target_coef_conf_int_low": float(ci.loc["opcount", 0]),
            "target_coef_conf_int_high": float(ci.loc["opcount", 1]),
        }
    )
    for extra in extras:
        row[f"{extra}_runtime_ms"] = float(fit.params[extra])
        row[f"{extra}_pvalue"] = float(fit.pvalues[extra])
        row[f"{extra}_conf_int_low"] = float(ci.loc[extra, 0])
        row[f"{extra}_conf_int_high"] = float(ci.loc[extra, 1])
    return row


def estimate_models(config: Config, fixtures_df: pd.DataFrame) -> EstimateOutput:
    """Fit one NNLS model per ``(spec, model_by-combo, client)``.

    Args:
        config: The validated configuration whose ``resolved_models`` drives
            iteration order.
        fixtures_df: The shared per-fixture frame produced by
            ``io.fixtures.build_fixtures_df``.

    Returns:
        :class:`EstimateOutput` carrying the canonical ``results.csv`` frame
        (one row per successful fit) and a parallel dict of fit objects.

    Raises:
        ConfigError: If the input opcount invariant is violated for any
            filtered fixture.
        ModelingError: If every fit across every spec is skipped.
    """
    rows: list[dict[str, object]] = []
    fits: dict[tuple, NNLSResults] = {}

    for spec in config.resolved_models:
        slice_df = fixtures_df[fixtures_df["test_name"] == spec.test_name]
        slice_df = _apply_filters(slice_df, spec.filter_by)
        if slice_df.empty:
            _log.warning(
                "spec test_name=%r had no matching fixtures after filter_by=%r; skipping",
                spec.test_name,
                spec.filter_by,
            )
            continue

        slice_df = _resolve_target_opcode(slice_df, spec)
        _enforce_opcount_invariant(slice_df, spec)
        slice_df = _materialize_derived(slice_df, spec)

        # Validate the model_by columns exist on the slice.
        for col in spec.model_by:
            if col not in slice_df.columns:
                raise ModelingError(
                    f"spec test_name={spec.test_name!r}: model_by column "
                    f"{col!r} is not present on the filtered fixtures"
                )

        # Group iteration. When model_by is empty there is one group: the whole slice.
        if spec.model_by:
            groups = slice_df.groupby(spec.model_by, dropna=False, sort=True)
        else:
            groups = [((), slice_df)]
        if hasattr(groups, "__iter__") and not isinstance(groups, list):
            group_iter = list(groups)
        else:
            group_iter = groups

        for group_key, group_df in group_iter:
            if not spec.model_by:
                group_values: dict[str, str] = {}
            else:
                if not isinstance(group_key, tuple):
                    key_tuple = (group_key,)
                else:
                    key_tuple = group_key
                group_values = {col: val for col, val in zip(spec.model_by, key_tuple)}
            group_label = "/".join(f"{k}={v}" for k, v in group_values.items()) or "all"

            target_opcodes = set(group_df["target_opcode"].unique())
            if len(target_opcodes) != 1:
                raise ModelingError(
                    f"spec test_name={spec.test_name!r} group={group_label}: "
                    f"multiple target_opcodes in one group: {sorted(target_opcodes)!r}"
                )
            target_opcode = next(iter(target_opcodes))

            for client, client_df in group_df.groupby("client_name", sort=True):
                design, extras = _build_design(client_df, spec)
                features = ["opcount"] + extras
                fit = _fit_or_skip(design, features, config, spec, client, group_label)
                if fit is None:
                    continue
                row = _build_result_row(
                    spec=spec,
                    client=client,
                    target_opcode=target_opcode,
                    group_values=group_values,
                    fit=fit,
                    extras=extras,
                )
                rows.append(row)
                fit_key = (
                    spec.source_label,
                    spec.test_name,
                    target_opcode,
                    *[group_values[c] for c in spec.model_by],
                    client,
                )
                fits[fit_key] = fit

    if not rows:
        raise ModelingError(
            "every model spec was skipped — no rows produced for results.csv"
        )

    results_df = pd.DataFrame(rows)
    # Deterministic ordering.
    sort_cols = ["test_name", "target_opcode"]
    sort_cols += sorted({c for spec in config.resolved_models for c in spec.model_by})
    sort_cols += ["client_name", "source_label"]
    sort_cols = [c for c in sort_cols if c in results_df.columns]
    results_df = results_df.sort_values(sort_cols, kind="mergesort").reset_index(
        drop=True
    )

    return EstimateOutput(results_df=results_df, fits=fits)
