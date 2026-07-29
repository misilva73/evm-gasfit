"""End-to-end coverage for the zkevm-benchmark-workload adapter.

The fixtures under ``tests/assets/zkevm_adapter`` are verbatim metrics records
sampled from the reth and ethrex openvm benchmark runs. Tests that need a
specific tree shape derive one from those records under ``tmp_path``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from evm_gasfit.adapter.zkevm import prepare_zkevm
from evm_gasfit.io.fixtures import build_fixtures_df
from evm_gasfit.io.opcounts import load_opcounts
from evm_gasfit.io.runtimes import load_runtimes

ASSETS = Path(__file__).resolve().parent / "assets" / "zkevm_adapter" / "zkevm-metrics"
_CLIENTS = {"reth-c5dff62-openvm-v2.0.0", "ethrex-e8860d2-openvm-v2.0.0"}
_RT_COLS = "client_name fixture_name test_runtime_ms source_path original_test_name"
_REASONS = {"proving_crashed", "missing_target_opcode"}


def _tree(tmp_path: Path, layout: dict[str, list[int]]) -> Path:
    """Derive a tree from one record: client to per-record ``STATICCALL`` tally."""
    template = json.loads(next(ASSETS.glob("*/*/*test_sha256*.json")).read_text())
    root = tmp_path / "tree"
    for client, tallies in layout.items():
        (root / client).mkdir(parents=True)
        for position, tally in enumerate(tallies):
            record = json.loads(json.dumps(template))
            record["metadata"]["opcode_count"]["STATICCALL"] = tally
            record["proving"]["success"]["proving_time_ms"] = 500 + position
            (root / client / f"r{position}.json").write_text(json.dumps(record))
    # An unrelated fixture keeps a run non-empty when the derived one is dropped.
    shutil.copy(next(ASSETS.glob("*/*/*test_codecopy*.json")), root / "other.json")
    return root


def test_cli_writes_inputs_the_pipeline_can_join(tmp_path: Path) -> None:
    out = tmp_path / "prepared"
    result = subprocess.run(
        [sys.executable, "-m", "evm_gasfit.cli", "prepare-zkevm"]
        + ["--zkevm-metrics", str(ASSETS), "--out", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"CLI exited {result.returncode}\n{result.stderr}"
    frame, match = build_fixtures_df(
        load_runtimes(out / "runtimes.csv"), load_opcounts(out / "opcounts.json")
    )
    assert not frame.empty and not match.only_runtimes and not match.only_opcounts


def test_prepares_inputs_from_real_records(tmp_path: Path) -> None:
    prepared = prepare_zkevm(ASSETS, tmp_path / "prepared")
    out = prepared.out_dir
    runtimes = pd.read_csv(out / "runtimes.csv")
    fixtures = pd.read_csv(out / "fixtures.csv")
    opcounts = json.loads((out / "opcounts.json").read_text())
    sha = next(entry for name, entry in opcounts.items() if "test_sha256" in name)
    counts = (prepared.runtimes_count, prepared.opcounts_count, prepared.excluded_count)
    assert counts == (2, 2, 2)
    assert list(runtimes.columns) == _RT_COLS.split()
    assert set(runtimes["client_name"]) == _CLIENTS
    assert len(opcounts) == len(runtimes) == 2
    # Precompiles trace only the enclosing STATICCALL.
    assert sha["opcount"] == sha["SHA2-256"] == sha["STATICCALL"]
    # fixtures.csv is per fixture, not per measurement.
    assert "client_name" not in fixtures.columns
    assert set(fixtures["block_limit_million"]) == {60}
    assert set(pd.read_csv(out / "excluded.csv")["reason"]) == _REASONS


@pytest.mark.parametrize(
    ("layout", "reasons", "kept"),
    [
        ({"a-client": [100], "b-client": [100]}, [], 2),
        ({"a-client": [100], "b-client": [101]}, ["inconsistent_opcounts"] * 2, 0),
        ({"a-client": [100, 200]}, ["inconsistent_opcounts"] * 2, 0),
        ({"a-client": [100, 100]}, ["duplicate_fixture_name"], 1),
    ],
    ids=["agree", "clients_disagree", "one_client_disagrees", "one_client_repeats"],
)
def test_fixture_identity_across_records(
    tmp_path: Path, layout: dict[str, list[int]], reasons: list[str], kept: int
) -> None:
    out = prepare_zkevm(_tree(tmp_path, layout), tmp_path / "prepared").out_dir
    runtimes = pd.read_csv(out / "runtimes.csv")
    opcounts = json.loads((out / "opcounts.json").read_text())

    assert pd.read_csv(out / "excluded.csv")["reason"].tolist() == reasons
    # One benchmark keeps one identity; a contradicted one keeps none.
    assert len([name for name in opcounts if "test_sha256" in name]) == min(kept, 1)
    assert runtimes["fixture_name"].str.contains("test_sha256").sum() == kept
