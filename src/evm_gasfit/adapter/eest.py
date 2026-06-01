"""Prepare EEST blockchain fixtures for ``evm-gasfit`` inputs."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evm_gasfit.errors import ConfigError

_BENCHMARK_GAS_RE = re.compile(r"(?:benchmark-)?gas-value_(?P<millions>\d+)M")

_PRECOMPILE_TARGETS = {
    "BLAKE2F",
    "BLS12_G1ADD",
    "BLS12_G1MSM",
    "BLS12_G2ADD",
    "BLS12_G2MSM",
    "BLS12_MAP_FP2_TO_G2",
    "BLS12_MAP_FP_TO_G1",
    "BLS12_PAIRING",
    "BLS12_PAIRING_CHECK",
    "BN128_ADD",
    "BN128_MUL",
    "BN128_PAIRING",
    "ECADD",
    "ECMUL",
    "ECPAIRING",
    "ECRECOVER",
    "IDENTITY",
    "MODEXP",
    "P256VERIFY",
    "POINT_EVALUATION",
    "RIPEMD-160",
    "SHA2-256",
}

_FIXTURE_COLUMNS = (
    "fixture_name",
    "original_test_name",
    "source_path",
    "block_index",
    "blocks_count",
    "network",
    "chain_id",
    "block_number",
    "block_used_gas",
    "block_limit_million",
    "target_opcode",
)
_EXCLUDED_COLUMNS = (
    "source_path",
    "original_test_name",
    "block_index",
    "reason",
    "details",
)


@dataclass(frozen=True)
class PreparedEest:
    """Summary of files written by :func:`prepare_eest`."""

    fixtures_count: int
    excluded_count: int
    out_dir: Path


@dataclass
class _FixtureRow:
    fixture_name: str
    original_test_name: str
    source_path: str
    block_index: int
    blocks_count: int
    network: str
    chain_id: str
    block_number: int | None
    block_used_gas: int | None
    block_limit_million: int | None
    target_opcode: str
    opcounts: dict[str, int | float]


@dataclass(frozen=True)
class _PreparedCase:
    target_opcode: str
    opcounts: dict[str, int | float]
    block_limit_million: int | None


def prepare_eest(eest_fixtures: Path, out_dir: Path) -> PreparedEest:
    """Normalize EEST ``blockchain_tests`` fixtures into gasfit-native files.

    Args:
        eest_fixtures: Root directory containing EEST fixture JSON files.
        out_dir: Destination directory. It is created if needed.

    Returns:
        Counts of included and excluded fixture rows.

    Raises:
        ConfigError: If the root does not exist or no usable fixtures are found.
    """
    root = Path(eest_fixtures)
    if not root.exists():
        raise ConfigError(f"EEST fixtures directory not found: {root}")
    if not root.is_dir():
        raise ConfigError(f"EEST fixtures path is not a directory: {root}")

    rows: list[_FixtureRow] = []
    excluded: list[dict[str, object]] = []
    seen_names: set[str] = set()

    for json_file in sorted(root.rglob("*.json")):
        if ".meta" in json_file.parts:
            continue
        source_path = _relative_source_path(json_file, root)
        try:
            raw = json.loads(json_file.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            excluded.append(
                _excluded(source_path, "", "", "invalid_json", str(exc))
            )
            continue

        if not isinstance(raw, dict):
            excluded.append(
                _excluded(
                    source_path,
                    "",
                    "",
                    "top_level_not_object",
                    "fixture JSON must be an object keyed by EEST test name",
                )
            )
            continue

        for original_test_name in sorted(raw):
            test_data = raw[original_test_name]
            if not isinstance(test_data, dict):
                excluded.append(
                    _excluded(
                        source_path,
                        original_test_name,
                        "",
                        "entry_not_object",
                        "top-level fixture entry is not an object",
                    )
                )
                continue

            prepared = _prepare_case(
                test_data=test_data,
                original_test_name=original_test_name,
                source_path=source_path,
            )
            if isinstance(prepared, dict):
                excluded.append(prepared)
                continue

            blocks = _blocks_for(test_data)
            if not blocks:
                excluded.append(
                    _excluded(
                        source_path,
                        original_test_name,
                        "",
                        "no_blocks",
                        "fixture entry has no blocks",
                    )
                )
                continue

            # Benchmark fixtures can contain setup or intermediate blocks; the
            # final block is the one that matters for measured benchmark work.
            block_index = len(blocks) - 1
            block = blocks[block_index]
            fixture_name = _native_fixture_name(
                original_test_name,
                prepared.block_limit_million,
            )
            if fixture_name in seen_names:
                fixture_name = _native_fixture_name(
                    original_test_name,
                    prepared.block_limit_million,
                    source_id=_source_id(source_path, original_test_name),
                )
            seen_names.add(fixture_name)

            rows.append(
                _FixtureRow(
                    fixture_name=fixture_name,
                    original_test_name=original_test_name,
                    source_path=source_path,
                    block_index=block_index,
                    blocks_count=len(blocks),
                    network=str(test_data.get("network") or ""),
                    chain_id=_chain_id(test_data),
                    block_number=_block_number(block),
                    block_used_gas=_block_used_gas(block),
                    block_limit_million=prepared.block_limit_million,
                    target_opcode=prepared.target_opcode,
                    opcounts=prepared.opcounts,
                )
            )

    if not rows:
        raise ConfigError(
            f"no usable EEST fixtures found under {root}; "
            "expected _info.metadata.opcode_count and _info.metadata.target_opcode"
        )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_opcounts(out / "opcounts.json", rows)
    _write_fixtures(out / "fixtures.csv", rows)
    _write_excluded(out / "excluded.csv", excluded)

    return PreparedEest(
        fixtures_count=len(rows),
        excluded_count=len(excluded),
        out_dir=out,
    )


def _prepare_case(
    *,
    test_data: dict[str, Any],
    original_test_name: str,
    source_path: str,
) -> _PreparedCase | dict[str, object]:
    info = test_data.get("_info", {})
    if not isinstance(info, dict):
        return _excluded(
            source_path,
            original_test_name,
            "",
            "missing_info",
            "fixture entry has no _info object",
        )

    metadata = info.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    target_opcode = metadata.get("target_opcode")
    if not isinstance(target_opcode, str) or not target_opcode:
        return _excluded(
            source_path,
            original_test_name,
            "",
            "missing_target_opcode",
            "fixture metadata has no target_opcode",
        )

    opcode_count = metadata.get("opcode_count")
    if opcode_count is None:
        opcode_count = info.get("opcode_count")
    if not isinstance(opcode_count, dict) or not opcode_count:
        return _excluded(
            source_path,
            original_test_name,
            "",
            "missing_opcode_count",
            "fixture metadata has no opcode_count object",
        )

    try:
        counts = {str(op): _coerce_count(count) for op, count in opcode_count.items()}
    except ValueError as exc:
        return _excluded(
            source_path,
            original_test_name,
            "",
            "invalid_opcode_count",
            str(exc),
        )

    target_count = counts.get(target_opcode)
    if target_count is None and target_opcode in _PRECOMPILE_TARGETS:
        target_count = counts.get("STATICCALL")
        if target_count is not None:
            counts[target_opcode] = target_count

    if target_count is None:
        return _excluded(
            source_path,
            original_test_name,
            "",
            "missing_target_opcount",
            f"target_opcode {target_opcode!r} is absent from opcode_count",
        )

    counts["opcount"] = target_count
    return _PreparedCase(
        target_opcode=target_opcode,
        opcounts=counts,
        block_limit_million=_block_limit_million(original_test_name),
    )


def _relative_source_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _blocks_for(test_data: dict[str, Any]) -> list[Any]:
    blocks = test_data.get("blocks")
    return blocks if isinstance(blocks, list) else []


def _chain_id(test_data: dict[str, Any]) -> str:
    config = test_data.get("config", {})
    if not isinstance(config, dict):
        return ""
    value = config.get("chainid")
    return "" if value is None else str(value)


def _block_number(block: Any) -> int | None:
    if not isinstance(block, dict):
        return None
    header = block.get("blockHeader", {})
    if isinstance(header, dict):
        parsed = _parse_int(header.get("number"))
        if parsed is not None:
            return parsed
    return _parse_int(block.get("blocknumber"))


def _block_used_gas(block: Any) -> int | None:
    if not isinstance(block, dict):
        return None
    header = block.get("blockHeader", {})
    if not isinstance(header, dict):
        return None
    return _parse_int(header.get("gasUsed"))


def _parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 16 if text.lower().startswith("0x") else 10)
        except ValueError:
            return None
    return None


def _block_limit_million(original_test_name: str) -> int | None:
    match = _BENCHMARK_GAS_RE.search(original_test_name)
    return int(match["millions"]) if match else None


def _native_fixture_name(
    original_test_name: str,
    block_limit_million: int | None,
    *,
    source_id: str | None = None,
) -> str:
    name = original_test_name.replace(".py::", ".py__", 1)
    tokens = []
    if block_limit_million is not None:
        tokens.append(f"block_limit_million_{block_limit_million}")
    if source_id is not None:
        tokens.append(f"source_id_{source_id}")

    if name.endswith("]") and "[" in name:
        base, raw_tokens = name.rsplit("[", 1)
        raw_tokens = raw_tokens[:-1]
        all_tokens = "-".join([part for part in [raw_tokens, *tokens] if part])
        return f"{base}[{all_tokens}]"
    return f"{name}[{'-'.join(tokens)}]"


def _source_id(source_path: str, original_test_name: str) -> str:
    digest = hashlib.sha1(f"{source_path}\0{original_test_name}".encode()).hexdigest()
    return digest[:8]


def _coerce_count(value: Any) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"opcode count must be numeric, got bool {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError as exc:
                raise ValueError(
                    f"opcode count must be numeric, got {value!r}"
                ) from exc
    raise ValueError(f"opcode count must be numeric, got {value!r}")


def _excluded(
    source_path: str,
    original_test_name: str,
    block_index: int | str,
    reason: str,
    details: str,
) -> dict[str, object]:
    return {
        "source_path": source_path,
        "original_test_name": original_test_name,
        "block_index": block_index,
        "reason": reason,
        "details": details,
    }


def _write_opcounts(path: Path, rows: list[_FixtureRow]) -> None:
    payload = {row.fixture_name: row.opcounts for row in rows}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_fixtures(path: Path, rows: list[_FixtureRow]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIXTURE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "fixture_name": row.fixture_name,
                    "original_test_name": row.original_test_name,
                    "source_path": row.source_path,
                    "block_index": row.block_index,
                    "blocks_count": row.blocks_count,
                    "network": row.network,
                    "chain_id": row.chain_id,
                    "block_number": _csv_value(row.block_number),
                    "block_used_gas": _csv_value(row.block_used_gas),
                    "block_limit_million": _csv_value(row.block_limit_million),
                    "target_opcode": row.target_opcode,
                }
            )


def _write_excluded(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_EXCLUDED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _csv_value(value: object | None) -> object:
    return "" if value is None else value
