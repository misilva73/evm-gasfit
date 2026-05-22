"""Hardcoded priced-glue opcode set and runtime-input validation.

The 12 priced opcodes split into two tiers (see plan §4.4): pure glue opcodes
have no glue dependency of their own and are fit one at a time; cycle glue
opcodes share a dependency cycle and are fit jointly.
"""

from __future__ import annotations

import pandas as pd

from evm_gasfit.errors import ConfigError


PURE_GLUE_OPCODES: list[str] = ["ISZERO", "JUMPDEST", "POP", "STOP", "SWAP"]

CYCLE_GLUE_OPCODES: list[str] = [
    "CALLDATASIZE",
    "DUP",
    "GAS",
    "MLOAD",
    "PUSH",
    "PUSH0",
    "STATICCALL",
]

PRICED_GLUE_OPCODES: list[str] = PURE_GLUE_OPCODES + CYCLE_GLUE_OPCODES

# (test_name, target_opcode) pairs the loader expects to see in the runtime
# inputs whenever glue adjustment is enabled.
REQUIRED_GLUE_TESTS: list[tuple[str, str]] = [(op, op) for op in PRICED_GLUE_OPCODES]


def validate_inputs(fixtures_df: pd.DataFrame) -> None:
    """Raise ``ConfigError`` if any required glue driver test is missing.

    Args:
        fixtures_df: Shared frame built by ``build_fixtures_df``.
    """
    present = set(fixtures_df["test_name"].unique())
    missing = sorted({t for t, _ in REQUIRED_GLUE_TESTS if t not in present})
    if missing:
        raise ConfigError(
            "glue adjustment enabled but the runtime inputs are missing driver "
            f"fixtures for test_name(s): {missing!r}"
        )
