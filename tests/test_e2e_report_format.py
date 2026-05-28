"""End-to-end: ``new_gas_proposal.md`` formatting + structural contracts.

Pins the report layout the user-facing proposal markdown produces:

- run-metadata line renders ``anchor_rate`` as ``<N> Mgas/s``;
- the ``## Proposed gas parameters`` table orders columns
  ``Gas param | Current gas | Proposed gas | Diff | Diff %`` with title-cased
  headers (no underscores);
- the ``Diff %`` cell carries a signed integer percent against ``current_gas``,
  and renders ``n/a`` for new params with no prior default;
- a ``## Client comparison`` section sits between ``## Proposed gas
  parameters`` and ``## Warnings`` and contains one row per multi-client
  gas param with worst + second-worst client values;
- ``### Unresolved (no fit)`` is a subsection of ``## Warnings`` (always
  present, ``_None._`` body when empty);
- ``### Partial fits (missing clients)`` is the next subsection inside
  ``## Warnings``: also always present, listing gas params that fit for at
  least one client but are missing for others;
- the per-client bar plot (``figs/proposal/by_client.png``) is no longer
  produced; only ``heatmap.png`` survives.
"""

from __future__ import annotations

from pathlib import Path

from _data_synth import (
    ClientModel,
    base_config,
    make_block_limit_fixtures,
    run_pipeline,
    write_standard_inputs,
)


def _build(tmp_path: Path, *, anchor_rate: float, plots: bool, new_params=None):
    fixtures = make_block_limit_fixtures(
        test_file="test_arithmetic",
        test_name="test_arithmetic",
        target_opcode="ADD",
        params={"opcode": "ADD"},
        target_opcount_per_million=500_000,
    )
    # Slopes sized so anchor_rate=1e8 * slope / 1000 yields well-spaced integer
    # gas values (geth=200, reth=220, besu=300) after ceil rounding.
    models = {
        "geth": ClientModel(intercept=80.0, slope=2.0e-3),
        "besu": ClientModel(intercept=100.0, slope=3.0e-3),
        "reth": ClientModel(intercept=90.0, slope=2.2e-3),
    }
    config = base_config(
        plots=plots,
        anchor_rate=anchor_rate,
        new_params=new_params,
    )
    return write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=config,
        noise_pct=0.001,
        seed=5,
    )


def test_anchor_rate_renders_as_mgas_per_second(tmp_path: Path) -> None:
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    assert "100 Mgas/s" in proposal, (
        "anchor_rate=1e8 should render as '100 Mgas/s' in the run-metadata line"
    )
    # The raw gas/second value must not leak through.
    assert "gas/s" not in proposal.replace("Mgas/s", "")


def test_proposed_table_column_order_and_headers(tmp_path: Path) -> None:
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    section = proposal.split("## Proposed gas parameters", 1)[1]
    section = section.split("##", 1)[0]
    header_line = next(
        line
        for line in section.splitlines()
        if line.startswith("|") and "Gas param" in line
    )
    cells = [c.strip() for c in header_line.strip("|").split("|")]
    assert cells == [
        "Gas param",
        "Current gas",
        "Proposed gas",
        "Diff",
        "Diff %",
    ], f"unexpected proposed-table header order: {cells}"
    # Headers must not contain literal underscores.
    for cell in cells:
        assert "_" not in cell, f"header {cell!r} retains an underscore"


def test_diff_percent_column_computed_against_current(tmp_path: Path) -> None:
    """For a raw fork field, the diff % matches the integer percent change
    between proposed and patched-current gas."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    section = proposal.split("## Proposed gas parameters", 1)[1].split("##", 1)[0]
    row = next(
        line
        for line in section.splitlines()
        if line.startswith("|") and "OPCODE_ADD" in line
    )
    cells = [c.strip() for c in row.strip("|").split("|")]
    # cells = [gas_param, current, proposed, diff, diff_pct]
    current = int(cells[1])
    proposed = int(cells[2])
    diff = int(cells[3].replace("+", ""))
    assert diff == proposed - current
    diff_pct_cell = cells[4]
    assert diff_pct_cell.endswith("%"), f"diff_pct cell missing %: {diff_pct_cell!r}"
    raw = diff_pct_cell.rstrip("%")
    expected = round((proposed - current) / current * 100)
    assert int(raw) == expected, (
        f"diff_pct cell {diff_pct_cell!r} doesn't match expected {expected:+d}%"
    )


def test_new_param_diff_pct_renders_na(tmp_path: Path) -> None:
    """A `new_params` entry with `null` baseline renders `n/a` in both the
    Diff and Diff % columns (no current_gas to ratio against)."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=False
    )
    # Replace the default OPCODE_ADD spec with one writing a fresh declared name.
    import yaml

    cfg = yaml.safe_load(config_yaml.read_text())
    cfg["models"]["custom"][0]["model_params"]["target_coef"] = "BRAND_NEW_PARAM"
    cfg["new_params"] = {"BRAND_NEW_PARAM": None}
    config_yaml.write_text(yaml.safe_dump(cfg, sort_keys=False))
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    section = proposal.split("## Proposed gas parameters", 1)[1].split("##", 1)[0]
    row = next(
        line
        for line in section.splitlines()
        if line.startswith("|") and "BRAND_NEW_PARAM" in line
    )
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[1] == "no prior default"
    assert cells[3] == "n/a"
    assert cells[4] == "n/a"


def test_client_comparison_section_present_and_populated(tmp_path: Path) -> None:
    """The `## Client comparison` section sits between `## Proposed gas
    parameters` and `## Warnings` and lists the worst vs. second-worst
    client per gas param."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    # Section ordering: Proposed -> Client comparison -> Warnings (with
    # Unresolved as the first subsection inside Warnings).
    idx_proposed = proposal.find("## Proposed gas parameters")
    idx_comparison = proposal.find("## Client comparison")
    idx_warnings = proposal.find("## Warnings")
    idx_unresolved = proposal.find("### Unresolved (no fit)")
    assert idx_proposed >= 0 and idx_comparison >= 0
    assert idx_warnings >= 0 and idx_unresolved >= 0
    assert idx_proposed < idx_comparison < idx_warnings < idx_unresolved
    # The old top-level `## Unresolved (no fit)` heading is gone; only the
    # subsection variant survives.
    assert "\n## Unresolved (no fit)" not in proposal

    section = proposal[idx_comparison:].split("##", 2)[1]
    # Header row.
    header_line = next(
        line
        for line in section.splitlines()
        if line.startswith("|") and "Worst client" in line
    )
    cells = [c.strip() for c in header_line.strip("|").split("|")]
    assert cells == [
        "Gas param",
        "Worst client",
        "Worst gas",
        "Second-worst client",
        "Second-worst gas",
        "Ratio",
    ], f"unexpected client-comparison header order: {cells}"

    # The OPCODE_ADD row carries the worst (besu, highest slope) ahead of
    # the second-worst (reth), with a ratio > 1×.
    add_row = next(
        line
        for line in section.splitlines()
        if line.startswith("|") and "OPCODE_ADD" in line
    )
    add_cells = [c.strip() for c in add_row.strip("|").split("|")]
    assert add_cells[0] == "OPCODE_ADD"
    assert add_cells[1] == "besu"
    assert add_cells[3] == "reth"
    worst_gas = int(add_cells[2])
    second_worst_gas = int(add_cells[4])
    assert worst_gas > second_worst_gas
    ratio_cell = add_cells[5]
    assert ratio_cell.endswith("×"), f"ratio cell missing ×: {ratio_cell!r}"
    ratio_value = float(ratio_cell.rstrip("×"))
    assert ratio_value > 1.0
    assert abs(ratio_value - worst_gas / second_worst_gas) < 0.01


def test_heatmap_embedded_in_client_comparison(tmp_path: Path) -> None:
    """When plots are enabled, the heatmap image embed lives inside the
    `## Client comparison` section, not in a trailing `## Plots` section."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=True
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    assert "![](figs/proposal/heatmap.png)" in proposal
    idx_comparison = proposal.find("## Client comparison")
    idx_warnings = proposal.find("## Warnings")
    idx_image = proposal.find("![](figs/proposal/heatmap.png)")
    assert idx_comparison < idx_image < idx_warnings, (
        "heatmap image should sit between Client comparison and Warnings"
    )

    assert (out_dir / "figs" / "proposal" / "heatmap.png").exists()
    # The by_client plot is dropped — must not be produced.
    assert not (out_dir / "figs" / "proposal" / "by_client.png").exists()
    # The old trailing "## Plots" section is also gone.
    assert "## Plots" not in proposal


def test_null_baseline_param_in_heatmap_emits_warning(tmp_path: Path) -> None:
    """A ``new_params`` entry with a ``null`` baseline that lands in the
    heatmap (i.e. a per-client fit produces a row for it) surfaces a
    ``null-baseline:`` warning under ``## Warnings``. The heatmap row itself
    will render blank since there is no current gas to ratio against."""
    import yaml

    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=True
    )
    cfg = yaml.safe_load(config_yaml.read_text())
    cfg["models"]["custom"][0]["model_params"]["target_coef"] = "BRAND_NEW_PARAM"
    cfg["new_params"] = {"BRAND_NEW_PARAM": None}
    config_yaml.write_text(yaml.safe_dump(cfg, sort_keys=False))
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    warnings_section = proposal.split("## Warnings", 1)[1]
    assert "null-baseline: new_params['BRAND_NEW_PARAM']" in warnings_section, (
        "expected null-baseline warning for BRAND_NEW_PARAM under ## Warnings"
    )
    assert (out_dir / "figs" / "proposal" / "heatmap.png").exists()


def test_null_baseline_warning_absent_when_all_baselines_known(
    tmp_path: Path,
) -> None:
    """Without any ``null`` ``new_params`` declarations, no ``null-baseline:``
    warning is emitted (the standard config writes to raw fork fields which
    always have a baseline)."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=True
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    assert "null-baseline:" not in proposal


def test_anchor_rate_three_sig_fig_smart_format(tmp_path: Path) -> None:
    """1.23e+08 gas/s -> 123 Mgas/s; 1.234e+08 -> 123 Mgas/s (3 sig figs)."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.234e8, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    assert "123 Mgas/s" in proposal, (
        "anchor_rate=1.234e8 should render as '123 Mgas/s' under 3-sig-fig formatting"
    )


def test_partial_fits_subsection_empty_renders_none(tmp_path: Path) -> None:
    """When every fitted gas param has an estimation from every client,
    ``### Partial fits (missing clients)`` still renders, with a ``_None._``
    body, immediately after ``### Unresolved (no fit)``."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build(
        tmp_path, anchor_rate=1.0e8, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    idx_unresolved = proposal.find("### Unresolved (no fit)")
    idx_partial = proposal.find("### Partial fits (missing clients)")
    assert idx_unresolved >= 0 and idx_partial >= 0
    assert idx_unresolved < idx_partial, (
        "Partial fits subsection must follow Unresolved inside Warnings"
    )
    body = proposal[idx_partial:].split("###", 2)
    section_body = body[1] if len(body) > 1 else body[0]
    assert "_None._" in section_body, (
        "empty Partial fits subsection should render `_None._`"
    )


def test_partial_fits_subsection_lists_missing_client_combos(tmp_path: Path) -> None:
    """When a gas param fits for some clients but not all, the missing
    (gas_param, client) combos surface under ``### Partial fits (missing
    clients)`` as ``| gas_param | missing_clients |`` rows."""
    import pandas as pd

    add_fixtures = make_block_limit_fixtures(
        test_file="test_arithmetic",
        test_name="test_arithmetic",
        target_opcode="ADD",
        params={"opcode": "ADD"},
        target_opcount_per_million=500_000,
    )
    sub_fixtures = make_block_limit_fixtures(
        test_file="test_arithmetic",
        test_name="test_arithmetic",
        target_opcode="SUB",
        params={"opcode": "SUB"},
        target_opcount_per_million=500_000,
    )
    fixtures = list(add_fixtures) + list(sub_fixtures)
    models = {
        "geth": ClientModel(intercept=80.0, slope=2.0e-3),
        "besu": ClientModel(intercept=100.0, slope=3.0e-3),
        "reth": ClientModel(intercept=90.0, slope=2.2e-3),
    }
    config = base_config(
        plots=False,
        anchor_rate=1.0e8,
        models_custom=[
            {
                "test_name": "test_arithmetic",
                "target_operation": "ADD",
                "filter_by": ["opcode_ADD-"],
                "model_params": {"target_coef": "OPCODE_ADD"},
            },
            {
                "test_name": "test_arithmetic",
                "target_operation": "SUB",
                "filter_by": ["opcode_SUB"],
                "model_params": {"target_coef": "OPCODE_SUB"},
            },
        ],
    )
    config_yaml, runtimes_csv, opcounts_json, out_dir = write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=config,
        noise_pct=0.001,
        seed=5,
    )
    # Drop besu's SUB rows so OPCODE_ADD fits on every client but OPCODE_SUB
    # fits only on geth + reth — the partial-fit scenario the subsection exists
    # to surface.
    runtimes = pd.read_csv(runtimes_csv)
    drop = (runtimes["client_name"] == "besu") & runtimes["fixture_name"].str.contains(
        "opcode_SUB"
    )
    runtimes[~drop].to_csv(runtimes_csv, index=False)

    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    idx_partial = proposal.find("### Partial fits (missing clients)")
    assert idx_partial >= 0, "Partial fits subsection missing"

    section_body = proposal[idx_partial:].split("###", 2)[1]
    # OPCODE_SUB has besu missing; render must call out both the param and
    # the missing client.
    sub_row = next(
        line
        for line in section_body.splitlines()
        if line.startswith("|") and "OPCODE_SUB" in line
    )
    assert "besu" in sub_row, f"OPCODE_SUB row should list besu as missing: {sub_row!r}"
    # OPCODE_ADD fits on every client — must not appear in this subsection.
    assert "OPCODE_ADD" not in section_body, (
        "OPCODE_ADD has fits for every client; should not be listed as partial"
    )
    # OPCODE_SUB still appears in the headline proposed-params table (it has
    # a value, just from a smaller pool of clients).
    proposed_section = proposal.split("## Proposed gas parameters", 1)[1].split(
        "##", 1
    )[0]
    assert "OPCODE_SUB" in proposed_section, (
        "OPCODE_SUB still has fits from geth + reth; it should appear in "
        "the Proposed gas parameters table"
    )


def _build_two_spec_shared_param(tmp_path: Path, *, plots: bool):
    """Two specs (ADD, SUB) writing to the same gas param ``OPCODE_GENERIC``
    plus one solo spec writing to ``OPCODE_MUL`` — exercises both the
    multi-combo (provenance heatmap) and single-combo (skip + italic) paths.
    """
    fixtures: list = []
    for opcode in ("ADD", "SUB", "MUL"):
        fixtures.extend(
            make_block_limit_fixtures(
                test_file="test_arithmetic",
                test_name="test_arithmetic",
                target_opcode=opcode,
                params={"opcode": opcode},
                target_opcount_per_million=500_000,
            )
        )
    models = {
        "geth": ClientModel(intercept=80.0, slope=2.0e-3),
        "besu": ClientModel(intercept=100.0, slope=3.0e-3),
        "reth": ClientModel(intercept=90.0, slope=2.2e-3),
    }
    config = base_config(
        plots=plots,
        anchor_rate=1.0e8,
        new_params={"OPCODE_GENERIC": 1},
        models_custom=[
            {
                "test_name": "test_arithmetic",
                "target_operation": "ADD",
                "filter_by": ["opcode_ADD-"],
                "model_params": {"target_coef": "OPCODE_GENERIC"},
            },
            {
                "test_name": "test_arithmetic",
                "target_operation": "SUB",
                "filter_by": ["opcode_SUB"],
                "model_params": {"target_coef": "OPCODE_GENERIC"},
            },
            {
                "test_name": "test_arithmetic",
                "target_operation": "MUL",
                "filter_by": ["opcode_MUL-"],
                "model_params": {"target_coef": "OPCODE_MUL"},
            },
        ],
    )
    return write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=config,
        noise_pct=0.001,
        seed=5,
    )


def test_provenance_section_present_with_per_param_heatmaps(tmp_path: Path) -> None:
    """Multi-combo gas params surface in `## Worst-case provenance per gas
    param` as a `<details>` block embedding a per-param heatmap PNG."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_two_spec_shared_param(
        tmp_path, plots=True
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    idx_comparison = proposal.find("## Client comparison")
    idx_provenance = proposal.find("## Worst-case provenance per gas param")
    idx_warnings = proposal.find("## Warnings")
    idx_overview_img = proposal.find("![](figs/proposal/heatmap.png)")
    assert idx_comparison >= 0 and idx_provenance >= 0 and idx_warnings >= 0
    assert idx_comparison < idx_overview_img < idx_provenance < idx_warnings, (
        "provenance section must sit after the overview heatmap embed "
        "and before ## Warnings"
    )

    section = proposal[idx_provenance:idx_warnings]
    # The multi-combo param renders as a <details> block embedding its PNG.
    assert "<details>" in section and "</details>" in section
    assert "OPCODE_GENERIC" in section
    assert "![](figs/proposal/provenance__OPCODE_GENERIC.png)" in section
    assert (out_dir / "figs" / "proposal" / "provenance__OPCODE_GENERIC.png").exists()


def test_provenance_section_skips_single_combo_params(tmp_path: Path) -> None:
    """Single-combo params do not get a `<details>` block or PNG; they are
    listed in a single italic line at the top of the section."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_two_spec_shared_param(
        tmp_path, plots=True
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    idx_provenance = proposal.find("## Worst-case provenance per gas param")
    idx_warnings = proposal.find("## Warnings")
    section = proposal[idx_provenance:idx_warnings]
    # OPCODE_MUL is fitted by a single spec → single combo → must be in the
    # skipped-params italic list, not a <details> block.
    skipped_line = next(
        line
        for line in section.splitlines()
        if line.startswith("_") and "OPCODE_MUL" in line
    )
    assert skipped_line.startswith("_") and skipped_line.rstrip().endswith("_")
    # No <summary> for OPCODE_MUL — the param does not get a collapsible.
    assert "<summary>" not in "\n".join(
        line for line in section.splitlines() if "OPCODE_MUL" in line
    )
    # No provenance PNG produced for it.
    assert not (out_dir / "figs" / "proposal" / "provenance__OPCODE_MUL.png").exists()


def test_provenance_section_renders_tables_when_plots_disabled(
    tmp_path: Path,
) -> None:
    """`output.plots: false` runs still surface the provenance section: each
    qualifying gas param gets a `<details>` block carrying a markdown table
    (combo rows × client columns) instead of an embedded PNG."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_two_spec_shared_param(
        tmp_path, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    idx_provenance = proposal.find("## Worst-case provenance per gas param")
    idx_warnings = proposal.find("## Warnings")
    assert idx_provenance >= 0, "provenance section must render even with plots off"
    section = proposal[idx_provenance:idx_warnings]
    # The multi-combo param renders a markdown table under its <details>.
    assert "<details>" in section and "OPCODE_GENERIC" in section
    assert "| Combo |" in section, "expected combo × clients markdown table"
    # No PNG embeds when plots are off.
    assert "![](figs/proposal/provenance__" not in section
    figs_dir = out_dir / "figs" / "proposal"
    if figs_dir.exists():
        assert not list(figs_dir.glob("provenance__*.png"))


def test_overview_table_replaces_heatmap_when_plots_disabled(
    tmp_path: Path,
) -> None:
    """`output.plots: false` swaps the overview heatmap embed for a markdown
    table (gas params as rows, clients as columns) inside `## Client
    comparison`. The heatmap PNG is not written."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_two_spec_shared_param(
        tmp_path, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    idx_comparison = proposal.find("## Client comparison")
    idx_provenance = proposal.find("## Worst-case provenance per gas param")
    section = proposal[idx_comparison:idx_provenance]
    assert "![](figs/proposal/heatmap.png)" not in section
    # The overview table has a `Gas param` header column followed by client names.
    assert "| Gas param | besu | geth | reth |" in section
    assert not (out_dir / "figs" / "proposal" / "heatmap.png").exists()


def test_contents_is_a_bulleted_toc(tmp_path: Path) -> None:
    """The TOC at the top of the proposal renders as a `## Contents` heading
    followed by a markdown bullet list. The provenance entry is conditional —
    present only when the worst-case provenance section actually renders."""
    config_yaml, runtimes_csv, opcounts_json, out_dir = _build_two_spec_shared_param(
        tmp_path, plots=False
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    proposal = (out_dir / "new_gas_proposal.md").read_text()
    assert "## Contents" in proposal
    assert "**Contents:**" not in proposal, "no leftover inline-list TOC"

    contents_section = proposal.split("## Contents", 1)[1].split("\n## ", 1)[0]
    bullets = [line for line in contents_section.splitlines() if line.startswith("- [")]
    assert bullets == [
        "- [Proposed parameters](#proposed-gas-parameters)",
        "- [Client comparison](#client-comparison)",
        "- [Worst-case provenance](#worst-case-provenance-per-gas-param)",
        "- [Warnings](#warnings)",
        "- [Poor-fit selections](#poor-fit-selections)",
    ]


def test_gas_params_follow_config_declaration_order(tmp_path: Path) -> None:
    """Proposed-params table, client-comparison table, and ``new_gas.csv``
    list gas params in the order their model first appears in ``models.custom``
    (followed by ``derived`` keys) — not alphabetically.

    Picks opcodes whose names are deliberately *not* in alphabetical order
    (SUB, ADD, MUL) so the assertion would fail under the old behavior.
    """
    import pandas as pd

    fixtures: list = []
    for opcode in ("ADD", "SUB", "MUL"):
        fixtures.extend(
            make_block_limit_fixtures(
                test_file="test_arithmetic",
                test_name="test_arithmetic",
                target_opcode=opcode,
                params={"opcode": opcode},
                target_opcount_per_million=500_000,
            )
        )
    models = {
        "geth": ClientModel(intercept=80.0, slope=2.0e-3),
        "besu": ClientModel(intercept=100.0, slope=3.0e-3),
        "reth": ClientModel(intercept=90.0, slope=2.2e-3),
    }
    config = base_config(
        plots=False,
        anchor_rate=1.0e8,
        models_custom=[
            {
                "test_name": "test_arithmetic",
                "target_operation": "SUB",
                "filter_by": ["opcode_SUB"],
                "model_params": {"target_coef": "OPCODE_SUB"},
            },
            {
                "test_name": "test_arithmetic",
                "target_operation": "ADD",
                "filter_by": ["opcode_ADD-"],
                "model_params": {"target_coef": "OPCODE_ADD"},
            },
            {
                "test_name": "test_arithmetic",
                "target_operation": "MUL",
                "filter_by": ["opcode_MUL-"],
                "model_params": {"target_coef": "OPCODE_MUL"},
            },
        ],
        extra={"derived": {"OPCODE_FOO": "OPCODE_ADD"}},
    )
    config_yaml, runtimes_csv, opcounts_json, out_dir = write_standard_inputs(
        tmp_path,
        fixtures=fixtures,
        models=models,
        config=config,
        noise_pct=0.001,
        seed=5,
    )
    run_pipeline(config_yaml, runtimes_csv, opcounts_json, out_dir)

    expected_order = ["OPCODE_SUB", "OPCODE_ADD", "OPCODE_MUL", "OPCODE_FOO"]

    proposal = (out_dir / "new_gas_proposal.md").read_text()

    proposed_section = proposal.split("## Proposed gas parameters", 1)[1].split(
        "##", 1
    )[0]
    proposed_order = [name for name in expected_order if name in proposed_section]
    proposed_positions = [proposed_section.find(name) for name in proposed_order]
    assert proposed_positions == sorted(proposed_positions), (
        f"Proposed gas parameters table out of config order: "
        f"{proposed_order} appear at {proposed_positions}"
    )

    comparison_section = proposal.split("## Client comparison", 1)[1].split("##", 1)[0]
    # Derived entries don't show in the comparison table (single placeholder
    # client row); only the fitted opcodes should be ordered there.
    fitted_order = [name for name in expected_order if name != "OPCODE_FOO"]
    comparison_positions = [comparison_section.find(name) for name in fitted_order]
    assert all(p >= 0 for p in comparison_positions), (
        f"missing rows in client-comparison: {fitted_order} -> {comparison_positions}"
    )
    assert comparison_positions == sorted(comparison_positions), (
        f"Client comparison table out of config order: positions {comparison_positions}"
    )

    new_gas = pd.read_csv(out_dir / "new_gas.csv")
    csv_order = [str(p) for p in new_gas["gas_param"]]
    assert csv_order == expected_order, (
        f"new_gas.csv gas_param column out of config order: {csv_order}"
    )
