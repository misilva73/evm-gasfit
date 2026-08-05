"""Prepare zkevm-benchmark-workload metrics for ``evm-gasfit`` inputs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm_gasfit.adapter.eest import (
    _EXCLUDED_COLUMNS,
    _block_limit_million,
    _csv_value,
    _excluded,
    _native_fixture_name,
    _parse_int,
    _resolve_opcounts,
)
from evm_gasfit.errors import ConfigError

_SKIP_NAMES = {"hardware.json"}

_RUNTIMES_COLUMNS = (
    "client_name",
    "fixture_name",
    "test_runtime_ms",
    "source_path",
    "original_test_name",
)
_FIXTURE_COLUMNS = (
    "fixture_name",
    "original_test_name",
    "source_path",
    "block_index",
    "network",
    "chain_id",
    "block_number",
    "block_used_gas",
    "block_limit_million",
    "target_opcode",
)


@dataclass(frozen=True)
class PreparedZkevm:
    """Summary of files written by :func:`prepare_zkevm`."""

    runtimes_count: int
    opcounts_count: int
    excluded_count: int
    out_dir: Path


@dataclass(frozen=True)
class _Fixture:
    """The client-independent facts about one benchmarked block."""

    original_test_name: str
    source_path: str
    block_index: int | None
    network: str
    chain_id: str
    block_number: int | None
    block_used_gas: int | None
    block_limit_million: int | None
    target_opcode: str
    opcounts: dict[str, int | float]


@dataclass(frozen=True)
class _Measurement:
    """One client's runtime for a fixture."""

    fixture_name: str
    client_name: str
    test_runtime_ms: int | float


def prepare_zkevm(zkevm_metrics: Path, out_dir: Path) -> PreparedZkevm:
    """Normalize a zkevm-benchmark-workload metrics tree into gasfit inputs.

    Args:
        zkevm_metrics: Root directory of per-test metrics JSON files. Each
            record's ``client_name`` is derived from its directory path below
            this root, so a ``<client>/<zkvm>`` layout keeps the provers apart.
        out_dir: Destination directory. It is created if needed.

    Returns:
        Counts of included and excluded records.

    Raises:
        ConfigError: If the root does not exist or no usable records are found.
    """
    root = Path(zkevm_metrics).resolve()
    if not root.exists():
        raise ConfigError(f"zkevm metrics directory not found: {root}")
    if not root.is_dir():
        raise ConfigError(f"zkevm metrics path is not a directory: {root}")

    fixtures: dict[str, _Fixture] = {}
    measurements: list[_Measurement] = []
    excluded: list[dict[str, object]] = []
    seen_names: set[tuple[str, str]] = set()
    conflicted: set[str] = set()

    for json_file in sorted(root.rglob("*.json")):
        if json_file.name in _SKIP_NAMES:
            continue
        file_rel = json_file.relative_to(root).as_posix()
        try:
            record = json.loads(json_file.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            excluded.append(_excluded(file_rel, "", "", "invalid_json", str(exc)))
            continue
        if not isinstance(record, dict):
            excluded.append(
                _excluded(
                    file_rel, "", "", "top_level_not_object", "record must be an object"
                )
            )
            continue

        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            excluded.append(
                _excluded(
                    file_rel,
                    "",
                    "",
                    "missing_metadata",
                    "record has no metadata object",
                )
            )
            continue

        source_path = str(metadata.get("source_path") or file_rel)
        original_test_name = metadata.get("original_test_name")
        if not isinstance(original_test_name, str) or not original_test_name:
            excluded.append(
                _excluded(
                    source_path,
                    "",
                    "",
                    "missing_test_name",
                    "metadata has no original_test_name",
                )
            )
            continue

        prepared = _resolve_opcounts(
            metadata.get("target_opcode"),
            metadata.get("opcode_count"),
            source_path,
            original_test_name,
        )
        if isinstance(prepared, dict):
            excluded.append(prepared)
            continue
        target_opcode, counts = prepared

        runtime = _proving_runtime(
            record.get("proving"), source_path, original_test_name
        )
        if isinstance(runtime, dict):
            excluded.append(runtime)
            continue

        block_index = _parse_int(metadata.get("block_index"))
        label = _client_label(json_file, root)
        block_limit_million = _block_limit_million(original_test_name)
        fixture_name = _native_fixture_name(original_test_name, block_limit_million)

        fixture = _Fixture(
            original_test_name=original_test_name,
            source_path=source_path,
            block_index=block_index,
            network=str(metadata.get("network") or ""),
            chain_id=_chain_id(metadata),
            block_number=_parse_int(metadata.get("block_number")),
            block_used_gas=_parse_int(metadata.get("block_used_gas")),
            block_limit_million=block_limit_million,
            target_opcode=target_opcode,
            opcounts=counts,
        )

        # Every record of a fixture describes the same block, so the target and
        # its opcode tally must agree no matter which client produced it. When
        # they disagree there is no basis for preferring one, so the fixture is
        # poisoned for every client, including records already accepted. This
        # precedes the duplicate check so contradictory records are never
        # mistaken for a benign repeat.
        known = fixtures.get(fixture_name)
        if fixture_name in conflicted or (
            known is not None
            and (
                known.target_opcode != fixture.target_opcode
                or known.opcounts != fixture.opcounts
            )
        ):
            conflicted.add(fixture_name)
            excluded.append(
                _excluded(
                    source_path,
                    original_test_name,
                    "" if block_index is None else block_index,
                    "inconsistent_opcounts",
                    f"target opcode or counts for {fixture_name!r} disagree with "
                    f"the record already accepted (client {label!r})",
                )
            )
            continue

        if (label, fixture_name) in seen_names:
            excluded.append(
                _excluded(
                    source_path,
                    original_test_name,
                    "" if block_index is None else block_index,
                    "duplicate_fixture_name",
                    f"{fixture_name!r} is already recorded for client {label!r}",
                )
            )
            continue

        seen_names.add((label, fixture_name))
        fixtures.setdefault(fixture_name, fixture)
        measurements.append(
            _Measurement(
                fixture_name=fixture_name,
                client_name=label,
                test_runtime_ms=runtime,
            )
        )

    for name in conflicted:
        fixture = fixtures.pop(name)
        for measurement in measurements:
            if measurement.fixture_name != name:
                continue
            excluded.append(
                _excluded(
                    fixture.source_path,
                    fixture.original_test_name,
                    "" if fixture.block_index is None else fixture.block_index,
                    "inconsistent_opcounts",
                    f"dropped with the rest of {name!r}, whose records disagree "
                    f"(client {measurement.client_name!r})",
                )
            )
    measurements = [m for m in measurements if m.fixture_name not in conflicted]

    if not measurements:
        raise ConfigError(
            f"no usable zkevm metrics found under {root}; expected metadata with "
            "target_opcode, opcode_count and a successful proving_time_ms"
        )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_opcounts(out / "opcounts.json", fixtures)
    _write_runtimes(out / "runtimes.csv", measurements, fixtures)
    _write_fixtures(out / "fixtures.csv", fixtures)
    _write_excluded(out / "excluded.csv", excluded)

    return PreparedZkevm(
        runtimes_count=len(measurements),
        opcounts_count=len(fixtures),
        excluded_count=len(excluded),
        out_dir=out,
    )


def _proving_runtime(
    proving: Any,
    source_path: str,
    original_test_name: str,
) -> int | float | dict[str, object]:
    if not isinstance(proving, dict):
        return _excluded(
            source_path,
            original_test_name,
            "",
            "missing_proving",
            "record has no proving object",
        )
    if "crashed" in proving:
        crashed = proving.get("crashed")
        reason = crashed.get("reason") if isinstance(crashed, dict) else ""
        return _excluded(
            source_path, original_test_name, "", "proving_crashed", str(reason or "")
        )
    success = proving.get("success")
    if not isinstance(success, dict):
        return _excluded(
            source_path,
            original_test_name,
            "",
            "unknown_proving_status",
            "proving has neither success nor crashed",
        )
    if success.get("output_matched") is False:
        return _excluded(
            source_path,
            original_test_name,
            "",
            "output_mismatch",
            "proving succeeded but output did not match the fixture",
        )
    proving_time = success.get("proving_time_ms")
    if not isinstance(proving_time, (int, float)) or isinstance(proving_time, bool):
        return _excluded(
            source_path,
            original_test_name,
            "",
            "missing_proving_time",
            "proving.success has no numeric proving_time_ms",
        )
    return proving_time


def _chain_id(metadata: dict[str, Any]) -> str:
    value = metadata.get("chain_id")
    return "" if value is None else str(value)


def _client_label(json_file: Path, root: Path) -> str:
    segments = json_file.relative_to(root).parts[:-1]
    return "-".join(segments) if segments else root.name


def _write_opcounts(path: Path, fixtures: dict[str, _Fixture]) -> None:
    payload = {name: fixture.opcounts for name, fixture in fixtures.items()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_runtimes(
    path: Path,
    measurements: list[_Measurement],
    fixtures: dict[str, _Fixture],
) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_RUNTIMES_COLUMNS)
        writer.writeheader()
        for measurement in measurements:
            fixture = fixtures[measurement.fixture_name]
            writer.writerow(
                {
                    "client_name": measurement.client_name,
                    "fixture_name": measurement.fixture_name,
                    "test_runtime_ms": measurement.test_runtime_ms,
                    "source_path": fixture.source_path,
                    "original_test_name": fixture.original_test_name,
                }
            )


def _write_fixtures(path: Path, fixtures: dict[str, _Fixture]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIXTURE_COLUMNS)
        writer.writeheader()
        for name, fixture in fixtures.items():
            writer.writerow(
                {
                    "fixture_name": name,
                    "original_test_name": fixture.original_test_name,
                    "source_path": fixture.source_path,
                    "block_index": _csv_value(fixture.block_index),
                    "network": fixture.network,
                    "chain_id": fixture.chain_id,
                    "block_number": _csv_value(fixture.block_number),
                    "block_used_gas": _csv_value(fixture.block_used_gas),
                    "block_limit_million": _csv_value(fixture.block_limit_million),
                    "target_opcode": fixture.target_opcode,
                }
            )


def _write_excluded(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_EXCLUDED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
