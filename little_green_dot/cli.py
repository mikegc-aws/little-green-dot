"""Command line entry point.

`little-green-dot` with no arguments starts the menu bar app. The other modes are
there so you can test the credential check — and diagnose a red dot — without a
GUI, which also makes the tool usable from scripts and shell prompts.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .aws import check_credentials, format_remaining
from .config import config_path, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="little-green-dot",
        description="Menu bar indicator for live AWS credentials.",
    )
    parser.add_argument("--once", action="store_true",
                        help="run one credential check, print the result, and exit")
    parser.add_argument("--json", action="store_true",
                        help="with --once, print machine-readable JSON")
    parser.add_argument("--config", metavar="PATH",
                        help="use this config file instead of the default")
    parser.add_argument("--where", action="store_true",
                        help="print the config file path and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.where:
        print(config_path())
        return 0

    path = None
    if args.config:
        from pathlib import Path

        path = Path(args.config).expanduser()
        if not path.exists():
            print(f"config file not found: {path}", file=sys.stderr)
            return 2

    config, warnings = load_config(path)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if args.once or args.json:
        return _run_once(config, as_json=args.json)

    # No flags: start the menu bar app. rumps is imported lazily so the checks
    # above still work on a machine without the GUI dependencies installed.
    from .app import LittleGreenDot

    LittleGreenDot(config, warnings).run()
    return 0


def _run_once(config, *, as_json: bool) -> int:
    check = check_credentials(config)

    if as_json:
        payload = {
            "state": check.state.value,
            "healthy": check.state.healthy,
            "checked_at": check.checked_at.isoformat(),
            "profile": check.profile,
            "region": check.region,
            "role": check.identity.name if check.identity else None,
            "arn": check.identity.arn if check.identity else None,
            "account": check.identity.account if check.identity else None,
            "expires_at": check.expires_at.isoformat() if check.expires_at else None,
            "expires_in_seconds": (
                int(check.remaining_seconds) if check.remaining_seconds is not None else None
            ),
            "error": check.error,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(check.summary)
        if check.identity is not None:
            print(f"  arn:     {check.identity.arn}")
            if check.expires_at:
                print(f"  expires: {check.expires_at.astimezone():%Y-%m-%d %H:%M:%S %Z}"
                      f" ({format_remaining(check.remaining_seconds)})")

    return 0 if check.state.healthy else 1
