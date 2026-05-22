"""Input loaders for runtimes CSV, opcounts JSON, and the fixture-name parser."""

from __future__ import annotations

import logging

logger = logging.getLogger("evm_gasfit")


def _format_unmatched(label: str, fixtures: list[str], limit: int = 20) -> str:
    shown = fixtures[:limit]
    extra = len(fixtures) - len(shown)
    body = ", ".join(sorted(shown))
    if extra > 0:
        body += f", and {extra} more"
    return f"{label}: {body}"


def report_unmatched_fixtures(
    runtimes_fixtures: set[str],
    opcounts_fixtures: set[str],
) -> set[str]:
    """Warn about fixtures present in only one input and return the matched set.

    Emits at most two warnings (one per direction) on the ``evm_gasfit`` logger,
    each capped at 20 names with an ``and N more`` suffix.
    """
    only_runtimes = sorted(runtimes_fixtures - opcounts_fixtures)
    only_opcounts = sorted(opcounts_fixtures - runtimes_fixtures)
    if only_runtimes:
        logger.warning(
            _format_unmatched(
                "fixtures in runtimes CSV but missing from opcounts JSON",
                only_runtimes,
            )
        )
    if only_opcounts:
        logger.warning(
            _format_unmatched(
                "fixtures in opcounts JSON but missing from runtimes CSV",
                only_opcounts,
            )
        )
    return runtimes_fixtures & opcounts_fixtures
