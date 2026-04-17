"""CLI entry point: python -m llm_local [fetch|replay|analyze]."""

import argparse
import logging
import sys

from llm_local.config import load_config
from llm_local.models import init_db


def main():
    parser = argparse.ArgumentParser(
        prog="llm_local",
        description="Local LLM crypto trading signal validation",
    )
    parser.add_argument(
        "--config", default="config.toml", help="Path to config file"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch and cache candle data")
    fetch_parser.add_argument(
        "--force", action="store_true", help="Re-fetch even if data exists in cache"
    )

    # replay command
    replay_parser = subparsers.add_parser("replay", help="Run historical replay")
    replay_parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode: use last 200 candles only (~30 min)",
    )
    replay_parser.add_argument(
        "--prompt-version", default="v1",
        help="Prompt template version to use (default: v1)",
    )

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze replay results")
    analyze_parser.add_argument(
        "--run-id", type=int, default=None,
        help="Specific run ID to analyze (default: latest)",
    )
    analyze_parser.add_argument(
        "--compare", action="store_true",
        help="Compare all completed runs side by side",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    engine = init_db(config.db_path)

    if args.command == "fetch":
        _cmd_fetch(config, engine, args)
    elif args.command == "replay":
        _cmd_replay(config, engine, args)
    elif args.command == "analyze":
        _cmd_analyze(config, engine, args)


def _cmd_fetch(config, engine, args):
    from llm_local.data import fetch_ohlcv, compute_indicators

    print(f"Fetching {config.days} days of {config.symbol} {config.timeframe} candles...")
    candles = fetch_ohlcv(config, engine, force=args.force)
    print(f"Fetched {len(candles)} candles")

    print("Computing technical indicators...")
    candles = compute_indicators(candles, engine)
    print(f"Indicators computed. {len(candles)} candles ready for replay.")


def _cmd_replay(config, engine, args):
    from llm_local.replay import run_replay

    mode = "QUICK" if args.quick else "FULL"
    print(f"Starting {mode} replay (prompt: {args.prompt_version})...")
    print(f"Checkpoint/resume enabled. Safe to interrupt.")
    print()

    run_id = run_replay(
        config,
        quick=args.quick,
        prompt_version=args.prompt_version,
        engine=engine,
    )

    print(f"\nReplay complete. Run ID: {run_id}")
    print(f"Run: python -m llm_local analyze --run-id {run_id}")


def _cmd_analyze(config, engine, args):
    from llm_local.analysis import analyze_run, format_report, compare_runs
    from llm_local.models import ReplayRun, get_session
    from sqlalchemy import select

    if args.compare:
        # Compare all completed runs
        with get_session(engine) as session:
            run_ids = session.execute(
                select(ReplayRun.id)
                .where(ReplayRun.status == "completed")
                .order_by(ReplayRun.id)
            ).scalars().all()

        if not run_ids:
            print("No completed replay runs found.")
            return

        print(compare_runs(list(run_ids), config, engine))
        return

    # Single run analysis
    run_id = args.run_id
    if run_id is None:
        # Find latest completed run
        with get_session(engine) as session:
            run_id = session.execute(
                select(ReplayRun.id)
                .where(ReplayRun.status == "completed")
                .order_by(ReplayRun.id.desc())
            ).scalars().first()

        if run_id is None:
            print("No completed replay runs found. Run 'replay' first.")
            return

    report = analyze_run(run_id, config, engine)
    print(format_report(report))


if __name__ == "__main__":
    main()
