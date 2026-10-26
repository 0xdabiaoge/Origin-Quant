"""Human-readable backtest report."""

from __future__ import annotations

from oq.backtest.engine import BacktestResult


def format_report(result: BacktestResult) -> str:
    m = result.metrics
    lines = [
        f"== {result.label} ==",
        (
            f"period          {_ts(result.start_ms)} -> {_ts(result.end_ms)}"
            f"  ({m.get('days', 0):.1f} days)"
        ),
        f"start / end     {m.get('start_equity', 0):,.2f} -> {m.get('end_equity', 0):,.2f}",
        f"total return    {m.get('total_return', 0):+.2%}",
        f"CAGR            {m.get('cagr', 0):+.2%}",
        f"max drawdown    {m.get('max_drawdown', 0):.2%}",
        f"Sharpe / Sortino {m.get('sharpe', 0):.2f} / {m.get('sortino', 0):.2f}",
        f"Calmar          {m.get('calmar', 0):.2f}",
        f"trades          {int(m.get('trades', 0))}   win rate {m.get('win_rate', 0):.1%}",
        f"profit factor   {m.get('profit_factor', 0):.2f}   payoff {m.get('payoff_ratio', 0):.2f}",
        f"avg win / loss  {m.get('avg_win', 0):,.2f} / {m.get('avg_loss', 0):,.2f}",
        f"avg win/loss R  {m.get('avg_win_r', 0):+.2f}R / {m.get('avg_loss_r', 0):+.2f}R",
        f"expectancy      {m.get('expectancy_r', 0):+.3f}R per trade",
        f"fees / funding  {m.get('fees', 0):,.2f} / {m.get('funding', 0):,.2f}",
        f"decisions       {result.decisions}   intents {result.intents}",
        f"open at end     {result.open_positions}",
    ]
    if result.rejections:
        lines.append("top rejections:")
        for reason, count in list(result.rejections.items())[:10]:
            lines.append(f"  {count:5d}  {reason[:110]}")
    if m.get("trades", 0):
        lines.append("")
        lines.append("last trades:")
        for trade in result.trades[-8:]:
            r = "" if trade.r_multiple is None else f" {trade.r_multiple:+.2f}R"
            lines.append(
                f"  {_ts(trade.exit_ms)} {trade.symbol:<16} {trade.book.value:<11} "
                f"{trade.reason:<12} pnl {trade.pnl:+10,.2f}{r}"
            )
    return "\n".join(lines)


def _ts(ms: int) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).strftime("%Y-%m-%d %H:%M")


__all__ = ["format_report"]
