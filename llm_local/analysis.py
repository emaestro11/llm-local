"""Analysis module: compute metrics from replay decisions.

Joins decisions to candle data to compute outcomes at analysis time.
No separate outcomes table needed for Phase 1.
"""

import logging
import math
from dataclasses import dataclass, field

from sqlalchemy import select

from llm_local.config import Config
from llm_local.models import Candle, Decision, ReplayRun, get_session, init_db

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """A completed trade: entry decision through exit."""

    entry_candle_id: int
    exit_candle_id: int
    entry_price: float
    exit_price: float
    size_pct: float
    confidence: float
    hold_candles: int
    exit_reason: str  # "sell", "force_close"
    gross_return: float = 0.0
    net_return: float = 0.0  # after fees


@dataclass
class Report:
    """Analysis report for a replay run."""

    run_id: int
    prompt_version: str
    total_candles: int
    total_decisions: int
    total_trades: int
    fallback_count: int

    # Trade metrics
    win_rate: float = 0.0
    avg_return: float = 0.0
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    simulated_pnl: float = 0.0

    # Confidence calibration
    confidence_correlation: float | None = None

    # Action distribution
    buy_count: int = 0
    sell_count: int = 0
    hold_count: int = 0

    trades: list[Trade] = field(default_factory=list)


def analyze_run(run_id: int, config: Config, engine=None) -> Report:
    """Analyze a completed replay run.

    Joins decisions to candles to reconstruct trades and compute metrics.
    """
    if engine is None:
        engine = init_db(config.db_path)

    # Load run metadata
    with get_session(engine) as session:
        run = session.get(ReplayRun, run_id)
        if run is None:
            raise ValueError(f"Replay run {run_id} not found")
        prompt_version = run.prompt_version
        session.expunge(run)

    # Load all decisions for this run, joined with candle data
    with get_session(engine) as session:
        decisions = session.execute(
            select(Decision, Candle)
            .join(Candle, Decision.candle_id == Candle.id)
            .where(Decision.run_id == run_id)
            .order_by(Candle.timestamp_ms)
        ).all()
        # Detach so objects are usable after session closes
        session.expunge_all()

    if not decisions:
        return Report(
            run_id=run_id,
            prompt_version=prompt_version,
            total_candles=0,
            total_decisions=0,
            total_trades=0,
            fallback_count=0,
        )

    # Reconstruct trades from decision sequence
    trades = _reconstruct_trades(decisions, config.fee_rate)

    # Count actions
    buy_count = sum(1 for d, _ in decisions if d.action == "buy")
    sell_count = sum(1 for d, _ in decisions if d.action in ("sell", "force_close"))
    hold_count = sum(1 for d, _ in decisions if d.action == "hold")
    fallback_count = sum(1 for d, _ in decisions if d.is_fallback)

    # Compute metrics
    report = Report(
        run_id=run_id,
        prompt_version=prompt_version,
        total_candles=len(decisions),
        total_decisions=len(decisions),
        total_trades=len(trades),
        fallback_count=fallback_count,
        buy_count=buy_count,
        sell_count=sell_count,
        hold_count=hold_count,
        trades=trades,
    )

    if trades:
        returns = [t.net_return for t in trades]
        winning = [r for r in returns if r > 0]

        report.win_rate = len(winning) / len(returns)
        report.avg_return = sum(returns) / len(returns)
        report.total_return = _compound_returns(returns)
        report.sharpe_ratio = _sharpe_ratio(returns)
        report.max_drawdown = _max_drawdown(returns)
        report.simulated_pnl = config.starting_capital * report.total_return

    # Confidence calibration
    report.confidence_correlation = _confidence_calibration(trades)

    return report


def _reconstruct_trades(
    decisions: list[tuple[Decision, Candle]],
    fee_rate: float,
) -> list[Trade]:
    """Reconstruct completed trades from a sequence of decisions.

    A trade starts with a 'buy' and ends with a 'sell' or 'force_close'.
    """
    trades = []
    entry_decision: Decision | None = None
    entry_candle: Candle | None = None

    for decision, candle in decisions:
        if decision.action == "buy" and entry_decision is None:
            entry_decision = decision
            entry_candle = candle

        elif decision.action in ("sell", "force_close") and entry_decision is not None:
            entry_price = entry_candle.close
            exit_price = candle.close
            gross_return = (exit_price - entry_price) / entry_price
            net_return = gross_return - 2 * fee_rate

            # Count candles between entry and exit
            hold_candles = 0
            counting = False
            for d, c in decisions:
                if c.id == entry_candle.id:
                    counting = True
                if counting:
                    hold_candles += 1
                if c.id == candle.id:
                    break

            trade = Trade(
                entry_candle_id=entry_candle.id,
                exit_candle_id=candle.id,
                entry_price=entry_price,
                exit_price=exit_price,
                size_pct=entry_decision.size_pct or 0.0,
                confidence=entry_decision.confidence or 0.0,
                hold_candles=hold_candles,
                exit_reason=decision.action,
                gross_return=gross_return,
                net_return=net_return,
            )
            trades.append(trade)

            entry_decision = None
            entry_candle = None

    return trades


def _compound_returns(returns: list[float]) -> float:
    """Compute compound return from a list of per-trade returns."""
    result = 1.0
    for r in returns:
        result *= (1 + r)
    return result - 1.0


def _sharpe_ratio(returns: list[float]) -> float:
    """Compute annualized Sharpe ratio from trade returns.

    Assumes ~35,000 15-min candles per year, and average hold of ~12 candles.
    """
    if len(returns) < 2:
        return 0.0

    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(variance)

    if std_r == 0:
        return 0.0

    # Estimate trades per year: ~35,040 15-min periods / avg hold
    avg_hold = 12  # candles per trade, rough estimate
    trades_per_year = 35040 / avg_hold

    return (mean_r / std_r) * math.sqrt(trades_per_year)


def _max_drawdown(returns: list[float]) -> float:
    """Compute max drawdown from cumulative trade returns."""
    if not returns:
        return 0.0

    cumulative = []
    equity = 1.0
    for r in returns:
        equity *= (1 + r)
        cumulative.append(equity)

    peak = cumulative[0]
    max_dd = 0.0

    for value in cumulative:
        if value > peak:
            peak = value
        dd = (peak - value) / peak
        if dd > max_dd:
            max_dd = dd

    return max_dd


def _confidence_calibration(trades: list[Trade]) -> float | None:
    """Compute Pearson correlation between entry confidence and trade return.

    Returns None if insufficient data or constant values.
    """
    if len(trades) < 3:
        return None

    confidences = [t.confidence for t in trades]
    returns = [t.net_return for t in trades]

    # Check for constant values
    if len(set(confidences)) < 2 or len(set(returns)) < 2:
        return None

    n = len(trades)
    mean_c = sum(confidences) / n
    mean_r = sum(returns) / n

    cov = sum((c - mean_c) * (r - mean_r) for c, r in zip(confidences, returns)) / n
    std_c = math.sqrt(sum((c - mean_c) ** 2 for c in confidences) / n)
    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / n)

    if std_c == 0 or std_r == 0:
        return None

    return cov / (std_c * std_r)


def format_report(report: Report) -> str:
    """Format a Report as a human-readable string for stdout."""
    lines = [
        f"{'=' * 60}",
        f"  REPLAY ANALYSIS REPORT (Run #{report.run_id})",
        f"{'=' * 60}",
        f"",
        f"  Prompt Version:  {report.prompt_version}",
        f"  Total Candles:   {report.total_candles}",
        f"  Total Decisions: {report.total_decisions}",
        f"  Fallbacks:       {report.fallback_count}",
        f"",
        f"  Action Distribution:",
        f"    BUY:  {report.buy_count:>5}  ({_pct(report.buy_count, report.total_decisions)})",
        f"    SELL: {report.sell_count:>5}  ({_pct(report.sell_count, report.total_decisions)})",
        f"    HOLD: {report.hold_count:>5}  ({_pct(report.hold_count, report.total_decisions)})",
        f"",
        f"  {'─' * 40}",
        f"  TRADE METRICS ({report.total_trades} trades)",
        f"  {'─' * 40}",
    ]

    if report.total_trades > 0:
        lines.extend([
            f"  Win Rate:        {report.win_rate:.1%}",
            f"  Avg Return:      {report.avg_return:.2%} per trade",
            f"  Total Return:    {report.total_return:.2%}",
            f"  Sharpe Ratio:    {report.sharpe_ratio:.2f} (annualized)",
            f"  Max Drawdown:    {report.max_drawdown:.2%}",
            f"  Simulated P&L:   ${report.simulated_pnl:,.2f}",
        ])
    else:
        lines.append("  No trades executed (all holds)")

    lines.extend([
        f"",
        f"  {'─' * 40}",
        f"  CONFIDENCE CALIBRATION",
        f"  {'─' * 40}",
    ])

    if report.confidence_correlation is not None:
        corr = report.confidence_correlation
        quality = (
            "GOOD" if corr > 0.2
            else "WEAK" if corr > 0.1
            else "NONE" if corr > 0
            else "INVERTED"
        )
        lines.append(f"  Pearson r:       {corr:.3f} ({quality})")
        if corr > 0.1:
            lines.append(f"  --> Confidence scores correlate with outcomes")
        else:
            lines.append(f"  --> Confidence scores do NOT predict outcomes")
    else:
        lines.append("  Insufficient data for calibration")

    lines.extend([
        f"",
        f"{'=' * 60}",
    ])

    return "\n".join(lines)


def compare_runs(run_ids: list[int], config: Config, engine=None) -> str:
    """Compare metrics across multiple replay runs side by side."""
    if engine is None:
        engine = init_db(config.db_path)

    reports = [analyze_run(rid, config, engine) for rid in run_ids]

    lines = [
        f"{'=' * 70}",
        f"  PROMPT COMPARISON ({len(reports)} runs)",
        f"{'=' * 70}",
        f"",
    ]

    header = f"  {'Metric':<25}"
    for r in reports:
        header += f"  {'Run #' + str(r.run_id) + ' (' + r.prompt_version + ')':<20}"
    lines.append(header)
    lines.append(f"  {'─' * 25}" + f"  {'─' * 20}" * len(reports))

    metrics = [
        ("Total Trades", lambda r: str(r.total_trades)),
        ("Win Rate", lambda r: f"{r.win_rate:.1%}" if r.total_trades else "N/A"),
        ("Avg Return", lambda r: f"{r.avg_return:.2%}" if r.total_trades else "N/A"),
        ("Total Return", lambda r: f"{r.total_return:.2%}" if r.total_trades else "N/A"),
        ("Sharpe Ratio", lambda r: f"{r.sharpe_ratio:.2f}" if r.total_trades else "N/A"),
        ("Max Drawdown", lambda r: f"{r.max_drawdown:.2%}" if r.total_trades else "N/A"),
        ("Confidence r", lambda r: f"{r.confidence_correlation:.3f}" if r.confidence_correlation is not None else "N/A"),
        ("Fallbacks", lambda r: str(r.fallback_count)),
    ]

    for name, fn in metrics:
        row = f"  {name:<25}"
        for r in reports:
            row += f"  {fn(r):<20}"
        lines.append(row)

    lines.extend([f"", f"{'=' * 70}"])

    return "\n".join(lines)


def _pct(count: int, total: int) -> str:
    """Format a count as a percentage of total."""
    if total == 0:
        return "0.0%"
    return f"{count / total:.1%}"
