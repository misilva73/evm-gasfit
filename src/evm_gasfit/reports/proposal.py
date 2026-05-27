"""Final proposal markdown: ``new_gas_proposal.md``."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from evm_gasfit.config import Config
from evm_gasfit.proposal.build import ProposalOutput

from .plots import plot_proposal_by_client, plot_proposal_heatmap

SENTINEL = "no prior default"

# `missing-glue: test_name='X' correlates with non-priced opcode 'Y'; ...`
_MISSING_GLUE_RE = re.compile(
    r"^missing-glue:\s*test_name=['\"]([^'\"]+)['\"]\s*"
    r"correlates with non-priced opcode\s*['\"]([^'\"]+)['\"]"
)


def _signed_diff(proposed: int, current: int) -> str:
    diff = proposed - current
    if diff == 0:
        return "0"
    return f"{diff:+d}"


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
    lines.append(
        f"_Generated {generated} · fork `{config.gas_costs.fork}` · "
        f"anchor_rate {config.anchor_rate:g} gas/s_"
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
        "[Unresolved (no fit)](#unresolved-no-fit)",
        "[Warnings](#warnings)",
        "[Poor-fit selections](#poor-fit-selections)",
    ]
    if config.output.plots:
        toc_items.append("[Plots](#plots)")
    lines.append("**Contents:** " + " · ".join(toc_items))
    lines.append("")

    # Diff table — fitted rows only.
    fitted_df = new_gas_df[~new_gas_df["new_gas_rounded"].isna()]
    unresolved_df = new_gas_df[new_gas_df["new_gas_rounded"].isna()]

    lines.append("## Proposed gas parameters")
    lines.append("")
    lines.append("| gas_param | proposed_gas | current_gas | diff |")
    lines.append("| --- | --- | --- | --- |")
    for _, row in fitted_df.iterrows():
        gas_param = str(row["gas_param"])
        proposed = int(row["new_gas_rounded"])
        if gas_param in current_values:
            current_int = int(current_values[gas_param])
            current_cell = str(current_int)
            diff_cell = _signed_diff(proposed, current_int)
        else:
            current_cell = SENTINEL
            diff_cell = "n/a"
        lines.append(f"| {gas_param} | {proposed} | {current_cell} | {diff_cell} |")
    lines.append("")

    # Unresolved (no fit) — placeholder rows from missing fits or None-derived.
    lines.append("## Unresolved (no fit)")
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
        lines.append("| gas_param |")
        lines.append("| --- |")
        for _, row in unresolved_df.iterrows():
            lines.append(f"| `{row['gas_param']}` |")
        lines.append("")

    # Warnings.
    lines.append("## Warnings")
    lines.append("")
    if not missing_by_test and not other_warnings:
        lines.append("_None._")
        lines.append("")
    else:
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
            lines.append("| test_name | non-priced opcodes |")
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
        lines.append("| gas_param | client | test_name |")
        lines.append("| --- | --- | --- |")
        for _, row in poor_fit_rows.iterrows():
            lines.append(
                f"| `{row['gas_param']}` | `{row['client_name']}` | "
                f"`{row['test_name']}` |"
            )
        lines.append("")

    # Plots.
    plots_enabled = config.output.plots
    new_gas_all_df = proposal_output.new_gas_all_df
    plottable = new_gas_all_df[new_gas_all_df["client_name"].astype(str).str.len() > 0]
    if plots_enabled and not plottable.empty:
        plot_proposal_heatmap(plottable, out_dir=out_dir)
        plot_proposal_by_client(plottable, out_dir=out_dir)
        lines.append("## Plots")
        lines.append("")
        lines.append("![](figs/proposal/heatmap.png)")
        lines.append("")
        lines.append("![](figs/proposal/by_client.png)")
        lines.append("")

    out_path.write_text("\n".join(lines))
