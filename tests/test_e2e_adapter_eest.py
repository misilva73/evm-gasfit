"""End-to-end coverage for the EEST adapter CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ASSETS_ROOT = (
    Path(__file__).resolve().parent / "assets" / "eest_adapter" / "blockchain_tests"
)


def _invoke_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    if shutil.which("evm-gasfit"):
        cmd = ["evm-gasfit", *args]
    else:
        cmd = [sys.executable, "-m", "evm_gasfit.cli", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_cli_prepare_eest_writes_native_inputs(tmp_path: Path) -> None:
    out = tmp_path / "prepared" / "eest"
    result = _invoke_cli(
        ["prepare-eest", "--eest-fixtures", str(ASSETS_ROOT), "--out", str(out)]
    )

    assert result.returncode == 0, (
        f"CLI exited with {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    opcounts = json.loads((out / "opcounts.json").read_text())
    assert len(opcounts) == 3

    expected_staticcall_counts = {1: 23, 2: 47, 3: 70}
    for block_limit_million, expected_count in expected_staticcall_counts.items():
        sha_fixture = next(
            name
            for name in opcounts
            if f"benchmark-gas-value_{block_limit_million}M" in name
        )
        assert f"block_limit_million_{block_limit_million}" in sha_fixture
        assert "block_index_" not in sha_fixture
        assert opcounts[sha_fixture]["opcount"] == expected_count
        # The `SHA2-256` opcode is automatically added by the EEST adapter, so that
        # the model validation of `opcount == <target opcode>` holds. The EEST
        # adapter does this because EEST opcode count only generates STATICCALLs
        # as the identified opcode when benchmarking precompiles.
        # This does not mean that `SHA2-256` and `STATICCALL` are different opcodes execution!
        assert opcounts[sha_fixture]["SHA2-256"] == expected_count
        assert opcounts[sha_fixture]["STATICCALL"] == expected_count

    fixtures = pd.read_csv(out / "fixtures.csv")
    assert len(fixtures) == 3
    assert set(fixtures["target_opcode"]) == {"SHA2-256"}
    assert set(fixtures["block_limit_million"]) == {1, 2, 3}
    assert set(fixtures["network"]) == {"Amsterdam"}
    assert set(fixtures["chain_id"]) == {"0x01"}
    assert set(fixtures["block_index"]) == {0}
    assert fixtures["source_path"].nunique() == 3
    assert set(fixtures["block_used_gas"]) == {1_000_000, 2_000_000, 3_000_000}

    excluded = pd.read_csv(out / "excluded.csv")
    assert excluded.empty
