"""Final proposal markdown: ``new_gas_proposal.md``."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from evm_gasfit.config import Config
from evm_gasfit.proposal.build import ProposalOutput

from .plots import plot_proposal_by_client, plot_proposal_heatmap

SENTINEL = "no prior default"


def _diff_cell(proposed: int, current: int | None) -> str:
    if current is None:
        return "n/a"
    return str(proposed - current)


def write_proposal_report(
    out_dir: Path,
    proposal_output: ProposalOutput,
    config: Config,
) -> None:
    """Write the final proposal markdown under ``out_dir``."""
    out_path = out_dir / "new_gas_proposal.md"
    new_gas_df = proposal_output.new_gas_df
    current_values = proposal_output.current_values

    lines: list[str] = ["# New gas proposal", ""]

    # Diff table.
    lines.append("## Proposed gas parameters")
    lines.append("")
    lines.append("| gas_param | proposed_gas | current_gas | diff |")
    lines.append("| --- | --- | --- | --- |")
    for _, row in new_gas_df.iterrows():
        gas_param = str(row["gas_param"])
        proposed = int(row["new_gas_rounded"])
        if gas_param in current_values:
            current_int = int(current_values[gas_param])
            current_cell = str(current_int)
            diff_cell = _diff_cell(proposed, current_int)
        else:
            current_cell = SENTINEL
            diff_cell = "n/a"
        lines.append(f"| {gas_param} | {proposed} | {current_cell} | {diff_cell} |")
    lines.append("")

    # Warnings section.
    lines.append("## Warnings")
    lines.append("")
    if proposal_output.warnings:
        for w in proposal_output.warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- (no warnings)")
    lines.append("")

    # Poor-fit notes from new_gas_all_df.
    poor_fit_rows = proposal_output.new_gas_all_df[
        proposal_output.new_gas_all_df.get("poor_fit", False) == True  # noqa: E712
    ]
    if not poor_fit_rows.empty:
        lines.append("## Poor-fit selections")
        lines.append("")
        for _, row in poor_fit_rows.iterrows():
            lines.append(
                f"- gas_param={row['gas_param']!r} client={row['client_name']!r} "
                f"test_name={row['test_name']!r}"
            )
        lines.append("")

    # Plots (if enabled and there are non-derived rows to plot).
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

    _ = np  # imported for future use; kept to mirror typing convention.
    _ = pd
    out_path.write_text("\n".join(lines))
