"""Fixture-name parser and shared ``fixtures_df`` builder."""

from __future__ import annotations

import re

import pandas as pd

from evm_gasfit.io import report_unmatched_fixtures

_FIXTURE_RE = re.compile(
    r"^(?P<test_file>[^.]+)\.py__(?P<test_name>[^\[]+)\[(?P<tokens>.*)\]$"
)
# Split a token into (key, value) at the first '_' whose value side starts with
# an uppercase letter or a digit — that's the key/value transition. If no such
# transition exists, fall back to the first '_'. Tokens with no '_' don't match
# and become standalone tags.
_KEY_VALUE_RE = re.compile(r"^(?P<key>.+?)_(?P<value>[A-Z0-9].*)$")


def parse_fixture_name(fixture_name: str) -> dict[str, str | list[str]]:
    """Parse an EEST fixture name into ``test_file``, ``test_name``, ``tokens``, and ``params``."""
    match = _FIXTURE_RE.match(fixture_name)
    if not match:
        return {
            "test_file": "",
            "test_name": fixture_name,
            "tokens": [],
            "params": {},
        }
    tokens = match["tokens"].split("-") if match["tokens"] else []
    params: dict[str, str] = {}
    for token in tokens:
        if "_" not in token:
            continue
        kv = _KEY_VALUE_RE.match(token)
        if kv is not None:
            params[kv["key"]] = kv["value"]
        else:
            key, _, value = token.partition("_")
            params[key] = value
    return {
        "test_file": match["test_file"],
        "test_name": match["test_name"],
        "tokens": tokens,
        "params": params,
    }


def build_fixtures_df(
    runtimes_df: pd.DataFrame,
    opcounts: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Build the shared ``fixtures_df`` joining runtimes with parsed params and opcounts.

    Args:
        runtimes_df: DataFrame from :func:`load_runtimes`, with at minimum the
            columns ``client_name``, ``fixture_name``, ``test_runtime_ms``.
        opcounts: Mapping from :func:`load_opcounts`.

    Returns:
        DataFrame with one row per ``(client_name, fixture_name)`` carrying:
        the original runtime columns, ``test_file`` and ``test_name`` from the
        parser, one column per parsed key/value param (string-valued;
        ``block_limit_million`` coerced to int), ``opcount``, and one column
        per opcode mnemonic appearing in any fixture (missing values filled
        with 0). Fixtures appearing in only one input are dropped with a
        single warning per direction on the ``evm_gasfit`` logger.
    """
    runtimes_fixtures = set(runtimes_df["fixture_name"].unique())
    opcounts_fixtures = set(opcounts.keys())
    matched = report_unmatched_fixtures(runtimes_fixtures, opcounts_fixtures)

    df = (
        runtimes_df[runtimes_df["fixture_name"].isin(matched)]
        .copy()
        .reset_index(drop=True)
    )

    # Parse fixture names once per unique fixture to avoid redundant work.
    unique_names = df["fixture_name"].drop_duplicates().tolist()
    parsed_rows = []
    for name in unique_names:
        parsed = parse_fixture_name(name)
        row: dict[str, object] = {
            "fixture_name": name,
            "test_file": parsed["test_file"],
            "test_name": parsed["test_name"],
        }
        row.update(parsed["params"])  # type: ignore[arg-type]
        parsed_rows.append(row)
    parsed_df = pd.DataFrame(parsed_rows)

    df = df.merge(parsed_df, on="fixture_name", how="left")

    if "block_limit_million" in df.columns:
        df["block_limit_million"] = df["block_limit_million"].astype(int)

    # Build a wide opcode-count table indexed by fixture_name and left-join it.
    opcounts_df = pd.DataFrame.from_dict(opcounts, orient="index")
    opcounts_df.index.name = "fixture_name"
    opcounts_df = opcounts_df.reset_index()
    # Restrict to matched fixtures so the merge can't reintroduce orphans.
    opcounts_df = opcounts_df[opcounts_df["fixture_name"].isin(matched)]
    # Per-opcode missing values are zero counts (sparse JSON is supported).
    opcode_cols = [c for c in opcounts_df.columns if c != "fixture_name"]
    opcounts_df[opcode_cols] = opcounts_df[opcode_cols].fillna(0.0)

    df = df.merge(opcounts_df, on="fixture_name", how="left")
    return df
