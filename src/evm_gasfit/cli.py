"""Command-line entry point: ``evm-gasfit run …``."""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

from evm_gasfit.api import GasFit
from evm_gasfit.errors import ConfigError, ModelingError

_log = logging.getLogger("evm_gasfit")


def _install_logging() -> None:
    root = logging.getLogger("evm_gasfit")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.WARNING)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evm-gasfit")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run the full estimation pipeline")
    run_p.add_argument("--config", required=True, type=Path)
    run_p.add_argument("--runtimes", required=True, type=Path)
    run_p.add_argument("--opcounts", required=True, type=Path)
    run_p.add_argument("--out", required=True, type=Path)
    run_p.set_defaults(func=_run)
    return parser


def _run(args: argparse.Namespace) -> int:
    fit = GasFit.from_config(Path(args.config))
    fit.load_runtimes(Path(args.runtimes))
    fit.load_opcounts(Path(args.opcounts))
    fit.estimate_models()
    if fit.config.glue_adjustment.enabled:
        fit.estimate_glue()
    fit.build_proposal()
    fit.write_reports(Path(args.out))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional override of ``sys.argv[1:]`` for tests.

    Returns:
        Process exit code (0 success, 1 config/input error, 2 modeling error).
    """
    _install_logging()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already wrote its error to stderr.
        return int(exc.code) if isinstance(exc.code, int) else 1

    try:
        return args.func(args)
    except ConfigError as exc:
        _log.error("config error: %s", exc)
        return 1
    except ModelingError as exc:
        _log.error("modeling error: %s", exc)
        return 2
    except Exception:
        _log.error("unexpected error:\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
