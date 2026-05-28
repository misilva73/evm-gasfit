"""Final proposal markdown: ``new_gas_proposal.md``."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from evm_gasfit.config import Config
from evm_gasfit.proposal.build import ProposalOutput

from .plots import plot_proposal_heatmap

SENTINEL = "no prior default"

# `missing-glue: test_name='X' correlates with non-priced opcode 'Y'; ...`
_MISSING_GLUE_RE = re.compile(
    r"^missing-glue:\s*test_name=['\"]([^'\"]+)['\"]\s*"
    r"correlates with non-priced opcode\s*['\"]([^'\"]+)['\"]"
)


def _format_anchor_rate_mgas_s(anchor_rate: float) -> str:
    """Render ``anchor_rate`` (gas/s) as a 3-sig-fig ``Mgas/s`` string."""
    mgas = float(anchor_rate) / 1e6
    if mgas == 0:
        return "0 Mgas/s"
    # 3 significant figures; strip trailing zeros after the decimal point so
    # round numbers stay readable (100 Mgas/s, not 100. Mgas/s).
    formatted = f"{mgas:.3g}"
    return f"{formatted} Mgas/s"


def _signed_diff(proposed: int, current: int) -> str:
    diff = proposed - current
    if diff == 0:
        return "0"
    return f"{diff:+d}"


def _signed_pct(proposed: int, current: int) -> str:
    if current == 0:
        return "n/a"
    pct = round((proposed - current) / current * 100)
    if pct == 0:
        return "0%"
    return f"{pct:+d}%"


def _direction_counts(
    new_gas_df, current_values: dict[str, int]
) -> tuple[int, int, int, int, int]:
    """Return (n_total, n_increased, n_decreased, n_new, n_unresolved)."""
    n_total = len(new_gas_df)
    inc = dec = new = unresolved = 0
    for _, row in new_gas_df.iterrows():
        gas_param = str(row["gas_param"])
        if pd.isna(row["new_gas_rounded"]):
            unresolved += 1
            continue
        proposed = int(row["new_gas_rounded"])
        if gas_param not in current_values:
            new += 1
            continue
        diff = proposed - int(current_values[gas_param])
        if diff > 0:
            inc += 1
        elif diff < 0:
            dec += 1
    return n_total, inc, dec, new, unresolved


def _partition_warnings(warnings: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split warnings into (missing-glue grouped by test_name) and (other)."""
    missing_by_test: dict[str, list[str]] = defaultdict(list)
    other: list[str] = []
    for w in warnings:
        m = _MISSING_GLUE_RE.match(w)
        if m:
            test_name, opcode = m.group(1), m.group(2)
            missing_by_test[test_name].append(opcode)
        else:
            other.append(w)
    return missing_by_test, other


def _build_partial_fit_rows(
    new_gas_all_df: pd.DataFrame,
) -> list[dict[str, object]]:
    """Per-param list of clients with no estimation, for params that fit on
    *some* clients.

    Fully-unresolved params (no client fit) are filtered out — they surface
    under the ``Unresolved (no fit)`` subsection. Derived/placeholder rows
    (empty ``client_name``) are likewise excluded.
    """
    df = new_gas_all_df[new_gas_all_df["client_name"].astype(str).str.len() > 0]
    df = df[df["new_gas_rounded"].notna()]
    if df.empty:
        return []
    clients_seen = sorted(set(df["client_name"].astype(str)))
    rows: list[dict[str, object]] = []
    # ``new_gas_all_df`` arrives in config-declaration order; preserve that
    # by iterating unique values in first-appearance order rather than sorting.
    for gas_param in dict.fromkeys(df["gas_param"].astype(str)):
        fitting_clients = set(
            df[df["gas_param"] == gas_param]["client_name"].astype(str)
        )
        missing = [c for c in clients_seen if c not in fitting_clients]
        if missing:
            rows.append({"gas_param": gas_param, "missing_clients": missing})
    return rows


def _build_client_comparison_rows(
    new_gas_all_df: pd.DataFrame,
    fitted_params: list[str],
) -> list[dict[str, object]]:
    """Worst vs. second-worst client per gas param, fitted rows only.

    Skips gas params with fewer than 2 fitted clients (nothing to compare).
    """
    df = new_gas_all_df[new_gas_all_df["client_name"].astype(str).str.len() > 0]
    df = df[df["new_gas_rounded"].notna()]
    rows: list[dict[str, object]] = []
    for gas_param in fitted_params:
        sub = df[df["gas_param"] == gas_param]
        if len(sub) < 2:
            continue
        ordered = sub.sort_values(
            by=["new_gas_rounded", "client_name"],
            ascending=[False, True],
            kind="mergesort",
        )
        worst = ordered.iloc[0]
        second = ordered.iloc[1]
        worst_val = int(worst["new_gas_rounded"])
        second_val = int(second["new_gas_rounded"])
        rows.append(
            {
                "gas_param": gas_param,
                "worst_client": str(worst["client_name"]),
                "worst_value": worst_val,
                "second_client": str(second["client_name"]),
                "second_value": second_val,
                "diff": _signed_diff(worst_val, second_val),
                "diff_pct": _signed_pct(worst_val, second_val),
            }
        )
    return rows


def write_proposal_report(
    out_dir: Path,
    proposal_output: ProposalOutput,
    config: Config,
) -> None:
    """Write the final proposal markdown under ``out_dir``."""
    out_path = out_dir / "new_gas_proposal.md"
    new_gas_df = proposal_output.new_gas_df
    current_values = proposal_output.current_values

    n_total, n_inc, n_dec, n_new, n_unresolved = _direction_counts(
        new_gas_df, current_values
    )
    missing_by_test, other_warnings = _partition_warnings(proposal_output.warnings)
    n_warn = sum(len(v) for v in missing_by_test.values()) + len(other_warnings)
    poor_fit_rows = proposal_output.new_gas_all_df[
        proposal_output.new_gas_all_df.get("poor_fit", False) == True  # noqa: E712
    ]

    lines: list[str] = ["# New gas proposal", ""]

    # Run metadata.
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    anchor_label = _format_anchor_rate_mgas_s(config.anchor_rate)
    lines.append(
        f"_Generated {generated} · fork `{config.gas_costs.fork}` · "
        f"anchor_rate {anchor_label}_"
    )
    lines.append("")

    # Summary line.
    lines.append(
        f"**Summary:** {n_total} parameters proposed — "
        f"{n_inc} increased, {n_dec} decreased, {n_new} new, "
        f"{n_unresolved} unresolved · "
        f"{n_warn} warning{'s' if n_warn != 1 else ''} · "
        f"{len(poor_fit_rows)} poor-fit selection{'s' if len(poor_fit_rows) != 1 else ''}"
    )
    lines.append("")

    # TOC.
    toc_items = [
        "[Proposed parameters](#proposed-gas-parameters)",
        "[Client comparison](#client-comparison)",
        "[Warnings](#warnings)",
        "[Poor-fit selections](#poor-fit-selections)",
    ]
    lines.append("**Contents:** " + " · ".join(toc_items))
    lines.append("")

    # Diff table — fitted rows only.
    fitted_df = new_gas_df[~new_gas_df["new_gas_rounded"].isna()]
    unresolved_df = new_gas_df[new_gas_df["new_gas_rounded"].isna()]

    lines.append("## Proposed gas parameters")
    lines.append("")
    lines.append("| Gas param | Current gas | Proposed gas | Diff | Diff % |")
    lines.append("| --- | --- | --- | --- | --- |")
    for _, row in fitted_df.iterrows():
        gas_param = str(row["gas_param"])
        proposed = int(row["new_gas_rounded"])
        if gas_param in current_values:
            current_int = int(current_values[gas_param])
            current_cell = str(current_int)
            diff_cell = _signed_diff(proposed, current_int)
            diff_pct_cell = _signed_pct(proposed, current_int)
        else:
            current_cell = SENTINEL
            diff_cell = "n/a"
            diff_pct_cell = "n/a"
        lines.append(
            f"| {gas_param} | {current_cell} | {proposed} | {diff_cell} | "
            f"{diff_pct_cell} |"
        )
    lines.append("")

    # Client comparison: worst vs. second-worst per gas param, plus heatmap.
    plots_enabled = config.output.plots
    new_gas_all_df = proposal_output.new_gas_all_df
    fitted_params = [str(p) for p in fitted_df["gas_param"]]
    comparison_rows = _build_client_comparison_rows(new_gas_all_df, fitted_params)

    lines.append("## Client comparison")
    lines.append("")
    if not comparison_rows:
        lines.append(
            "_Not enough clients to compare — every gas parameter was fitted "
            "by a single client._"
        )
        lines.append("")
    else:
        lines.append(
            "Worst client vs. second-worst client per gas parameter. The diff "
            "columns quantify how much each parameter would drop if priced "
            "against the second-worst client instead of the worst."
        )
        lines.append("")
        lines.append(
            "| Gas param | Worst client | Worst gas | Second-worst client | "
            "Second-worst gas | Diff | Diff % |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in comparison_rows:
            lines.append(
                f"| {row['gas_param']} | {row['worst_client']} | "
                f"{row['worst_value']} | {row['second_client']} | "
                f"{row['second_value']} | {row['diff']} | {row['diff_pct']} |"
            )
        lines.append("")

    plottable = new_gas_all_df[new_gas_all_df["client_name"].astype(str).str.len() > 0]
    if plots_enabled and not plottable.empty:
        plot_proposal_heatmap(plottable, out_dir=out_dir)
        lines.append("![](figs/proposal/heatmap.png)")
        lines.append("")

    # Warnings (with Unresolved as a subsection — always shown).
    lines.append("## Warnings")
    lines.append("")
    lines.append("### Unresolved (no fit)")
    lines.append("")
    if unresolved_df.empty:
        lines.append("_None._")
        lines.append("")
    else:
        lines.append(
            "These names were proposed by a `model_params` RHS or `derived` "
            "entry but produced no value — either every candidate fit was "
            "skipped (constant opcount, insufficient observations, solver "
            "failure) or a referenced upstream value was itself unresolved. "
            "Inspect the `evm_gasfit` warnings in `meta.json` for the cause."
        )
        lines.append("")
        lines.append("| Gas param |")
        lines.append("| --- |")
        for _, row in unresolved_df.iterrows():
            lines.append(f"| `{row['gas_param']}` |")
        lines.append("")

    # Partial fits: gas params with at least one client fit but missing on
    # others — the proposed value still stands but was selected from a
    # smaller pool.
    partial_rows = _build_partial_fit_rows(proposal_output.new_gas_all_df)
    lines.append("### Partial fits (missing clients)")
    lines.append("")
    if not partial_rows:
        lines.append("_None._")
        lines.append("")
    else:
        lines.append(
            "These gas parameters were fit by at least one client but not "
            "by every client — the listed clients produced no estimation, "
            "so the worst-case value was selected from a smaller pool. "
            "Inspect the `evm_gasfit` warnings in `meta.json` for the cause."
        )
        lines.append("")
        lines.append("| Gas param | Missing clients |")
        lines.append("| --- | --- |")
        for row in partial_rows:
            missing_cell = ", ".join(f"`{c}`" for c in row["missing_clients"])
            lines.append(f"| `{row['gas_param']}` | {missing_cell} |")
        lines.append("")
    if missing_by_test or other_warnings:
        if missing_by_test:
            lines.append("### Missing glue opcodes")
            lines.append("")
            lines.append(
                "The opcodes below correlate with the target opcount but are "
                "outside the priced glue set, so the target coefficient was "
                "left unadjusted. Consider adding them to the glue model or "
                "re-designing the test to isolate the target opcode."
            )
            lines.append("")
            lines.append("| Test name | Non-priced opcodes |")
            lines.append("| --- | --- |")
            for test_name in sorted(missing_by_test):
                opcodes = sorted(set(missing_by_test[test_name]))
                opcode_cell = ", ".join(f"`{op}`" for op in opcodes)
                lines.append(f"| `{test_name}` | {opcode_cell} |")
            lines.append("")
        if other_warnings:
            lines.append("### Other")
            lines.append("")
            for w in other_warnings:
                lines.append(f"- {w}")
            lines.append("")

    # Poor-fit.
    lines.append("## Poor-fit selections")
    lines.append("")
    if poor_fit_rows.empty:
        lines.append("_None._")
        lines.append("")
    else:
        lines.append(
            "Rows where the winning fit's p-value exceeded "
            f"`modeling.poor_fit_p_value_threshold` "
            f"({config.modeling.poor_fit_p_value_threshold:g}). The selection "
            "still stands but the headline coefficient is not statistically "
            "distinguishable from zero — review the underlying regression "
            "before relying on the proposed value."
        )
        lines.append("")
        lines.append("| Gas param | Client | Test name |")
        lines.append("| --- | --- | --- |")
        for _, row in poor_fit_rows.iterrows():
            lines.append(
                f"| `{row['gas_param']}` | `{row['client_name']}` | "
                f"`{row['test_name']}` |"
            )
        lines.append("")

    out_path.write_text("\n".join(lines))
