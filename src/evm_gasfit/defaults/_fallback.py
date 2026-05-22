"""Literal per-fork gas-cost tables used when the optional execution-specs extra is absent."""

from __future__ import annotations

_AMSTERDAM: dict[str, int] = {
    # Arithmetic opcodes
    "OPCODE_ADD": 3,
    "OPCODE_SUB": 3,
    "OPCODE_MUL": 5,
    "OPCODE_DIV": 5,
    # Storage access
    "COLD_STORAGE_ACCESS": 2100,
    "COLD_STORAGE_WRITE": 20000,
    "STORAGE_WRITE": 20000,
    "STORAGE_CLEAR_REFUND": 4800,
    # Account access
    "COLD_ACCOUNT_ACCESS": 2600,
    "COLD_ACCOUNT_CODE_ACCESS": 2600,
    "COLD_ACCOUNT_WRITE": 25000,
    "ACCOUNT_WRITE": 25000,
    # Warm / access list
    "GAS_WARM_ACCESS": 100,
    "ACCESS_LIST_STORAGE_KEY": 1900,
    "ACCESS_LIST_ADDRESS": 2400,
    # Misc Amsterdam-fork entries (placeholders so e2e tests have room to
    # exercise both fork-known and proposal-introduced names)
    "OPCODE_ADD_ALT": 3,
    "OPCODE_NEW": 8,
}

_OSAKA: dict[str, int] = {
    **_AMSTERDAM,
    # Osaka tweaks a handful of values; the exact numbers don't matter for the
    # tests, only that fork selection actually changes the table.
    "OPCODE_ADD": 4,
    "COLD_STORAGE_ACCESS": 2200,
    "COLD_ACCOUNT_ACCESS": 2700,
    "GAS_WARM_ACCESS": 150,
}

_TABLES: dict[str, dict[str, int]] = {
    "amsterdam": _AMSTERDAM,
    "osaka": _OSAKA,
}


def known_forks() -> list[str]:
    """Names of forks that have a fallback table."""
    return sorted(_TABLES)


def get_fallback(fork: str) -> dict[str, int]:
    """Return a fresh copy of the fallback gas-cost table for ``fork``.

    Raises:
        KeyError: when ``fork`` is not in the bundled fallback set.
    """
    return dict(_TABLES[fork])
