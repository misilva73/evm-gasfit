"""Plot helpers for runtime, glue, and proposal reports."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg", force=False)

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
import statsmodels.api as sm  # noqa: E402

from ..modeling.results import NNLSResults  # noqa: E402

logger = logging.getLogger("evm_gasfit.reports")

_FIGSIZE = (8.0, 5.0)
_DIAG_FIGSIZE = (12.0, 5.0)
_DPI = 100
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_UNDERSCORE_RUN = re.compile(r"_+")


def slug(*segments: str) -> str:
    """Sanitize each segment and join with ``__``.

    Per-segment, replaces non-alphanumeric characters with ``_``, collapses
    runs of ``_``, and trims edges. Sanitized segments are then joined with
    the literal ``__`` so the separator is never folded into a segment.
    """
    cleaned: list[str] = []
    for seg in segments:
        replaced = _NON_ALNUM.sub("_", seg)
        collapsed = _UNDERSCORE_RUN.sub("_", replaced).strip("_")
        cleaned.append(collapsed)
    return "__".join(cleaned)


def _ensure(out_dir: Path, family: str) -> Path:
    target = out_dir / "figs" / family
    target.mkdir(parents=True, exist_ok=True)
    return target


def _save(fig: plt.Figure, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=_DPI)
    plt.close(fig)
    return path


# Runtime per-fit plots --------------------------------------------------------


def plot_regression(
    fit: NNLSResults,
    *,
    target_opcode: str,
    test_name: str,
    model_by_combo: str,
    client: str,
    out_dir: Path,
) -> Path:
    figs_dir = _ensure(out_dir, "runtime")
    name = slug(target_opcode, test_name, model_by_combo, client, "regression")
    path = figs_dir / f"{name}.png"

    feature_names = fit._feature_names
    extras = [f for f in feature_names if f not in {"const", "opcount"}]
    opcount = fit._X[:, feature_names.index("opcount")]
    y = fit._y
    const = float(fit.params["const"])
    slope = float(fit.params["opcount"])

    if not extras:
        fig, ax = plt.subplots(figsize=_FIGSIZE)
        sns.scatterplot(x=opcount, y=y, ax=ax, alpha=0.7)
        x_range = np.linspace(float(opcount.min()), float(opcount.max()), 100)
        ax.plot(
            x_range,
            const + slope * x_range,
            color="red",
            linewidth=2,
            label="NNLS fit",
        )
        ax.set_xlabel("opcount")
        ax.set_ylabel("test_runtime_ms")
        ax.set_title(
            f"{target_opcode} / {test_name} / {client}\n"
            f"intercept={const:.2f}, slope={slope:.2e}"
        )
        ax.legend()
        return _save(fig, path)

    fig, axes = plt.subplots(
        1, len(extras), figsize=(4 * len(extras), 4), squeeze=False
    )
    axes = axes[0]
    palette = sns.color_palette("Set2", n_colors=8)
    for ax, feature in zip(axes, extras):
        # _X[:, f_idx] = opcount * p_i, so dividing recovers the raw p_i column.
        p_vals = fit._X[:, feature_names.index(feature)] / opcount
        feature_coef = float(fit.params[feature])
        for i, p in enumerate(sorted(set(p_vals.tolist()))):
            mask = p_vals == p
            color = palette[i % len(palette)]
            ax.scatter(
                opcount[mask],
                y[mask],
                color=color,
                alpha=0.6,
                s=20,
                label=f"{feature}={p:g}",
            )
            x_sub = opcount[mask]
            x_range = np.array([float(x_sub.min()), float(x_sub.max())])
            ax.plot(
                x_range,
                const + slope * x_range + feature_coef * p * x_range,
                color=color,
                linestyle="--",
                linewidth=2,
                alpha=0.8,
            )
        ax.set_xlabel("opcount")
        ax.set_ylabel("test_runtime_ms")
        ax.set_title(f"{feature}: coef={feature_coef:.2e}")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.3)
    fig.suptitle(
        f"{target_opcode} / {test_name} / {client}\n"
        f"intercept={const:.2f}, opcount coef={slope:.2e}"
    )
    return _save(fig, path)


def plot_bootstrap(
    fit: NNLSResults,
    *,
    target_opcode: str,
    test_name: str,
    model_by_combo: str,
    client: str,
    out_dir: Path,
) -> Path:
    figs_dir = _ensure(out_dir, "runtime")
    name = slug(target_opcode, test_name, model_by_combo, client, "bootstrap")
    path = figs_dir / f"{name}.png"

    try:
        idx = fit._feature_names.index(target_opcode)
    except ValueError:
        idx = len(fit._feature_names) - 1
    samples = fit._bootstrap_coefs[:, idx]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    sns.histplot(samples, kde=True, ax=ax)
    ax.axvline(float(fit.params.iloc[idx]), color="red", linestyle="--", linewidth=1)
    ax.set_xlabel(f"Bootstrap coefficient for {target_opcode}")
    ax.set_ylabel("Count")
    ax.set_title(f"{target_opcode} / {test_name} / {client} - bootstrap")
    return _save(fig, path)


def plot_diagnostics(
    fit: NNLSResults,
    *,
    target_opcode: str,
    test_name: str,
    model_by_combo: str,
    client: str,
    out_dir: Path,
) -> Path:
    figs_dir = _ensure(out_dir, "runtime")
    name = slug(target_opcode, test_name, model_by_combo, client, "diagnostics")
    path = figs_dir / f"{name}.png"

    fig, axes = plt.subplots(1, 2, figsize=_DIAG_FIGSIZE)
    sns.scatterplot(x=fit.fittedvalues, y=fit.resid, ax=axes[0], alpha=0.7)
    axes[0].axhline(0.0, color="black", linestyle="--", linewidth=1)
    axes[0].set_xlabel("Fitted")
    axes[0].set_ylabel("Residual")
    axes[0].set_title("Residuals vs fitted")

    sm.qqplot(fit.resid, line="45", fit=True, ax=axes[1])
    axes[1].set_title("Normal QQ")

    fig.suptitle(f"{target_opcode} / {test_name} / {client} - diagnostics")
    return _save(fig, path)


# Glue per-fit plots -----------------------------------------------------------


def plot_glue_regression(
    fit: NNLSResults,
    *,
    glue_opcode: str,
    client: str,
    out_dir: Path,
) -> Path:
    figs_dir = _ensure(out_dir, "glue")
    path = figs_dir / f"{slug(glue_opcode, client, 'regression')}.png"

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    sns.scatterplot(x=fit.fittedvalues, y=fit._y, ax=ax, alpha=0.7)
    lo = float(min(np.min(fit.fittedvalues), np.min(fit._y)))
    hi = float(max(np.max(fit.fittedvalues), np.max(fit._y)))
    ax.plot([lo, hi], [lo, hi], color="black", linestyle="--", linewidth=1)
    ax.set_xlabel("Fitted")
    ax.set_ylabel("Observed")
    ax.set_title(f"{glue_opcode} / {client} - glue regression")
    return _save(fig, path)


def plot_glue_bootstrap(
    fit: NNLSResults,
    *,
    glue_opcode: str,
    client: str,
    out_dir: Path,
) -> Path:
    figs_dir = _ensure(out_dir, "glue")
    path = figs_dir / f"{slug(glue_opcode, client, 'bootstrap')}.png"

    try:
        idx = fit._feature_names.index(glue_opcode)
    except ValueError:
        idx = len(fit._feature_names) - 1
    samples = fit._bootstrap_coefs[:, idx]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    sns.histplot(samples, kde=True, ax=ax)
    ax.axvline(float(fit.params.iloc[idx]), color="red", linestyle="--", linewidth=1)
    ax.set_xlabel(f"Bootstrap coefficient for {glue_opcode}")
    ax.set_ylabel("Count")
    ax.set_title(f"{glue_opcode} / {client} - glue bootstrap")
    return _save(fig, path)


def plot_glue_diagnostics(
    fit: NNLSResults,
    *,
    glue_opcode: str,
    client: str,
    out_dir: Path,
) -> Path:
    figs_dir = _ensure(out_dir, "glue")
    path = figs_dir / f"{slug(glue_opcode, client, 'diagnostics')}.png"

    fig, axes = plt.subplots(1, 2, figsize=_DIAG_FIGSIZE)
    sns.scatterplot(x=fit.fittedvalues, y=fit.resid, ax=axes[0], alpha=0.7)
    axes[0].axhline(0.0, color="black", linestyle="--", linewidth=1)
    axes[0].set_xlabel("Fitted")
    axes[0].set_ylabel("Residual")
    axes[0].set_title("Residuals vs fitted")

    sm.qqplot(fit.resid, line="45", fit=True, ax=axes[1])
    axes[1].set_title("Normal QQ")

    fig.suptitle(f"{glue_opcode} / {client} - glue diagnostics")
    return _save(fig, path)


# Proposal summary plots -------------------------------------------------------


def plot_proposal_heatmap(
    new_gas_all_df: pd.DataFrame,
    *,
    current_values: dict[str, int],
    out_dir: Path,
) -> Path:
    """Heatmap colored by ``log2(proposed / current)`` per cell.

    Red cells are more expensive than the current gas cost for that parameter,
    green cells are cheaper, and white sits at ``log2 = 0`` (unchanged). Rows
    whose ``gas_param`` has no entry in ``current_values`` — e.g. ``new_params``
    declared with a ``null`` baseline — render as blank cells with only the
    proposed integer annotation. The colorbar scale is symmetric and auto-sized
    to the largest absolute log2 ratio in the data (floored at ``±1`` so
    near-uniform runs still get a visible gradient).
    """
    figs_dir = _ensure(out_dir, "proposal")
    path = figs_dir / "heatmap.png"

    # ``new_gas_rounded`` is nullable Int64 to carry no-fit placeholders;
    # matplotlib only handles float NaN, so cast before pivoting.
    plot_df = new_gas_all_df.assign(
        new_gas_rounded=new_gas_all_df["new_gas_rounded"]
        .astype("Float64")
        .astype(float)
    )
    pivot = plot_df.pivot_table(
        index="gas_param",
        columns="client_name",
        values="new_gas_rounded",
        aggfunc="max",
    )
    # Preserve the caller's gas-param ordering (config declaration order, set
    # in ``proposal/build.py``) — ``pivot_table`` re-sorts the index by default.
    row_order = list(dict.fromkeys(plot_df["gas_param"].astype(str)))
    pivot = pivot.reindex([p for p in row_order if p in pivot.index])

    current = pd.Series(
        {idx: current_values.get(idx, np.nan) for idx in pivot.index},
        index=pivot.index,
        dtype="float64",
    )
    # log2(0) and log2(x/0) are non-finite; mask them so the colorbar stays
    # bounded and seaborn renders the cells as blank.
    ratio = pivot.div(current, axis=0)
    normalized = np.log2(ratio.where(ratio > 0))
    normalized = normalized.replace([np.inf, -np.inf], np.nan)

    finite = normalized.to_numpy()
    finite = finite[np.isfinite(finite)]
    bound = max(float(np.max(np.abs(finite))) if finite.size else 1.0, 1.0)

    width = max(_FIGSIZE[0], 1.2 * len(pivot.columns) + 4)
    height = max(_FIGSIZE[1], 0.4 * len(pivot.index) + 2)
    fig, ax = plt.subplots(figsize=(width, height))
    sns.heatmap(
        normalized,
        annot=pivot,
        fmt=".0f",
        cmap="RdYlGn_r",
        vmin=-bound,
        vmax=bound,
        center=0.0,
        cbar_kws={"label": "log2(proposed / current)"},
        ax=ax,
    )
    ax.set_title("Proposed gas vs. current (log2 ratio)")
    return _save(fig, path)
