"""Backtesting command entrypoint scaffold.

This CLI is intentionally non-running for now: it validates config and prints
planned artifact paths, but does not execute historical AI or portfolio replay.
"""

import argparse

from backend.src.backtesting.config import BacktestConfig
from backend.src.backtesting.reporting import run_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.src.backtesting.cli")
    parser.add_argument("command", choices=["plan"], help="Validate and print a no-cost run plan")
    args = parser.parse_args(argv)
    if args.command == "plan":
        config = BacktestConfig()
        manifest = run_manifest(config)
        print(f"run_id={manifest['run_id']}")
        print(f"config_artifact={manifest['artifacts']['config']}")
        print("live_ai_replay=disabled")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
