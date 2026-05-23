"""Priced-glue opcode specs and runtime-input validation.

Each spec binds a canonical glue name (``ISZERO``, ``DUP``, ``STATICCALL``,
...) to the driver fixture that produces its runtime estimate. Family opcodes
(``DUP``, ``SWAP``, ``PUSH``) are fit jointly over all members and share a
single coefficient: ``DUP1``..``DUP16`` all map to the canonical ``DUP``
estimate. ``POP`` and ``STOP`` are tracked as priced glue but have no driver
fixture yet, so they are skipped at fit time with a warning rather than an
error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from evm_gasfit.errors import ConfigError

_log = logging.getLogger("evm_gasfit.glue")


@dataclass(frozen=True)
class GlueOpcodeSpec:
    """One priced-glue family.

    Attributes:
        name: Canonical glue name emitted on every output frame
            (``glue_results_df``, ``glue_opcodes_by_test_df``). For families
            this is the family prefix (``DUP``, ``SWAP``, ``PUSH``); for
            singletons it is the opcode mnemonic.
        tier: Drives the fit routine. ``"pure"`` opcodes get a single-feature
            NNLS each; ``"cycle"`` opcodes share a joint per-client fit.
        test_name: ``test_name`` value to filter driver fixtures by. ``None``
            when no driver fixture exists yet — the spec is then skipped at
            fit time and ``validate_inputs`` does not require it.
        members: Opcode mnemonics in the fixture column space that belong to
            this family. ``("STATICCALL",)`` for singletons; the full member
            tuple for families. Row-wise summed into a synthetic
            ``name``-keyed column when building the design matrix.
        test_opcode_filter: Optional disambiguator when ``test_name`` is
            shared across specs (``MLOAD`` vs the other ops in
            ``test_memory_access``; ``PUSH0`` vs ``PUSH1..PUSH32`` inside
            ``test_push``). ``None`` when ``members`` alone is enough.
        required: ``False`` for specs without a driver fixture (POP, STOP);
            ``validate_inputs`` will not raise on their absence.
    """

    name: str
    tier: Literal["pure", "cycle"]
    test_name: str | None
    members: tuple[str, ...]
    test_opcode_filter: str | None = None
    required: bool = True


_DUP_MEMBERS: tuple[str, ...] = tuple(f"DUP{i}" for i in range(1, 17))
_SWAP_MEMBERS: tuple[str, ...] = tuple(f"SWAP{i}" for i in range(1, 17))
_PUSH_MEMBERS: tuple[str, ...] = tuple(f"PUSH{i}" for i in range(1, 33))


PRICED_GLUE_SPECS: tuple[GlueOpcodeSpec, ...] = (
    # Pure glue
    GlueOpcodeSpec("ISZERO", "pure", "test_iszero", ("ISZERO",)),
    GlueOpcodeSpec("JUMPDEST", "pure", "test_jumpdests", ("JUMPDEST",)),
    GlueOpcodeSpec("POP", "pure", None, ("POP",), required=False),
    GlueOpcodeSpec("STOP", "pure", None, ("STOP",), required=False),
    GlueOpcodeSpec("SWAP", "pure", "test_swap", _SWAP_MEMBERS),
    # Cycle glue
    GlueOpcodeSpec("CALLDATASIZE", "cycle", "test_calldatasize", ("CALLDATASIZE",)),
    GlueOpcodeSpec("DUP", "cycle", "test_dup", _DUP_MEMBERS),
    GlueOpcodeSpec("GAS", "cycle", "test_gas_op", ("GAS",)),
    GlueOpcodeSpec(
        "MLOAD", "cycle", "test_memory_access", ("MLOAD",), test_opcode_filter="MLOAD"
    ),
    GlueOpcodeSpec("PUSH", "cycle", "test_push", _PUSH_MEMBERS),
    GlueOpcodeSpec(
        "PUSH0", "cycle", "test_push", ("PUSH0",), test_opcode_filter="PUSH0"
    ),
    GlueOpcodeSpec(
        "STATICCALL",
        "cycle",
        "test_ext_account_query_warm",
        ("STATICCALL",),
        test_opcode_filter="STATICCALL",
    ),
)


# Canonical-name views, kept for callers that only care about names.
PURE_GLUE_OPCODES: list[str] = [s.name for s in PRICED_GLUE_SPECS if s.tier == "pure"]
CYCLE_GLUE_OPCODES: list[str] = [s.name for s in PRICED_GLUE_SPECS if s.tier == "cycle"]
PRICED_GLUE_OPCODES: list[str] = PURE_GLUE_OPCODES + CYCLE_GLUE_OPCODES


def _build_member_to_canonical() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for spec in PRICED_GLUE_SPECS:
        for member in spec.members:
            existing = mapping.get(member)
            if existing is not None and existing != spec.name:
                raise RuntimeError(
                    f"glue member {member!r} mapped to both {existing!r} and "
                    f"{spec.name!r} — spec table is inconsistent"
                )
            mapping[member] = spec.name
    return mapping


# Member opcode mnemonic → canonical family name (e.g. "DUP3" → "DUP").
MEMBER_TO_CANONICAL: dict[str, str] = _build_member_to_canonical()


def validate_inputs(fixtures_df: pd.DataFrame) -> None:
    """Validate that every required driver fixture is present.

    Required specs (``spec.required is True`` and ``spec.test_name is not
    None``) must appear in ``fixtures_df["test_name"]``; missing ones raise
    ``ConfigError``. Optional specs whose driver is absent are skipped
    silently — the fit loop will not produce a row for them.

    Args:
        fixtures_df: Shared frame built by ``build_fixtures_df``.
    """
    present = set(fixtures_df["test_name"].unique())
    missing_required = sorted(
        {
            spec.test_name
            for spec in PRICED_GLUE_SPECS
            if spec.required
            and spec.test_name is not None
            and spec.test_name not in present
        }
    )
    if missing_required:
        raise ConfigError(
            "glue adjustment enabled but the runtime inputs are missing driver "
            f"fixtures for test_name(s): {missing_required!r}"
        )

    missing_optional = sorted(
        {
            spec.test_name
            for spec in PRICED_GLUE_SPECS
            if not spec.required
            and spec.test_name is not None
            and spec.test_name not in present
        }
    )
    if missing_optional:
        _log.warning(
            "glue adjustment: optional driver fixtures missing for test_name(s): "
            "%r; corresponding opcodes will not be priced as glue",
            missing_optional,
        )
