"""Backtester — replay historical candles through the deterministic engine.

Walk-forward simulation: at every bar, a rolling window is evaluated by the
strategy engine; a confirmed plan is only filled if the *next* bar opens inside
the entry zone (or the current bar's close is inside), and is closed intrabar
when the stop or first take-profit is printed. Trades are independent — no
compounding, one open position at a time per symbol.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.trading.strategy_engine import StrategySpec, evaluate_signal
from app.services.trading.trade_plan import build_trade_plan

__all__ = ["BacktestReport", "BacktestTrade", "run_backtest"]

WINDOW = 140


@dataclass
class BacktestTrade:
    bar_in: int
    bar_out: int
    direction: str
    entry: float
    exit: float
    reason: str
    pnl: float

    @property
    def rr(self) -> float | None:
        return None


@dataclass
class BacktestReport:
    symbol: str
    timeframe: str
    strategy_key: str
    candles_used: int
    trades: list[BacktestTrade]

    def summary(self) -> dict:
        t = self.trades
        if not t:
            return {
                "total_trades": 0, "win_rate": 0.0, "profit_factor": None,
                "net_pnl": 0.0, "max_drawdown": 0.0, "consecutive_losses": 0,
            }
        wins = [x for x in t if x.pnl > 0]
        gross_win = sum(x.pnl for x in wins)
        gross_loss = abs(sum(x.pnl for x in t if x.pnl <= 0))
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        streak = worst_streak = 0
        for x in t:
            equity += x.pnl
            if x.pnl > 0:
                streak = 0
            else:
                streak += 1
                worst_streak = max(worst_streak, streak)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)
        return {
            "total_trades": len(t),
            "win_rate": round(len(wins) / len(t) * 100, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "net_pnl": round(equity, 2),
            "max_drawdown": round(max_dd * 100, 2),
            "avg_holding_bars": round(sum(x.bar_out - x.bar_in for x in t) / len(t), 1),
            "consecutive_losses": worst_streak,
        }


def _fill_price(open_price: float, low: float, high: float, plan) -> float | None:
    zone = (plan.entry_low, plan.entry_high)
    if zone[0] <= open_price <= zone[1]:
        return open_price
    return None


def run_backtest(
    candles: list,
    spec: StrategySpec,
    *,
    symbol: str,
    timeframe: str = "4h",
    fee_bps: float = 5.0,
    direction_override: str | None = None,
) -> BacktestReport:
    """Run a deterministic walk-forward backtest over the candle list."""
    if len(candles) < WINDOW + 2:
        return BacktestReport(symbol, timeframe, spec.key, len(candles), trades=[])

    trades: list[BacktestTrade] = []
    i = WINDOW
    while i < len(candles) - 1:
        window = candles[i - WINDOW:i + 1]
        signal = evaluate_signal(symbol, timeframe, spec, window, direction_override=direction_override)
        plan = build_trade_plan(signal, spec)
        if plan is None:
            i += 1
            continue
        fill = _fill_price(candles[i + 1].open, candles[i + 1].low, candles[i + 1].high, plan)
        if fill is None:
            i += 1
            continue

        entry = fill
        sl = plan.stop_loss
        tp = plan.take_profits[0]
        entry_idx = i + 1
        exit_price = exit_reason = None
        j = entry_idx
        while j < len(candles):
            c = candles[j]
            if plan.direction == "LONG":
                if sl is not None and c.low <= sl:
                    exit_price, exit_reason = sl, "STOP"
                    break
                if tp is not None and c.high >= tp:
                    exit_price, exit_reason = tp, "TP"
                    break
            else:
                if sl is not None and c.high >= sl:
                    exit_price, exit_reason = sl, "STOP"
                    break
                if tp is not None and c.low <= tp:
                    exit_price, exit_reason = tp, "TP"
                    break
            j += 1
        if exit_price is None:
            c = candles[-1]
            exit_price = c.close
            exit_reason = "CLOSE"
            j = len(candles) - 1

        gross = (
            (exit_price - entry) if plan.direction == "LONG"
            else (entry - exit_price)
        )
        fee = exit_price * (fee_bps / 10_000.0) + entry * (fee_bps / 10_000.0)
        trades.append(BacktestTrade(entry_idx, j, plan.direction, entry, exit_price, exit_reason, round(gross - fee, 2)))
        i = j + 1  # no re-entry until this trade is closed

    return BacktestReport(symbol, timeframe, spec.key, len(candles), trades)