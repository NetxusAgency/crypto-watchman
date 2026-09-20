"""Deterministic indicator extensions for the trading engine.

Reuses the assistant indicator primitives (EMA/SMA/RSI/MACD/ATR/Bollinger/S-R)
and adds the two engines this stack needs: Stochastic oscillator and Fibonacci
retracement levels.
"""

from typing import Sequence

from app.services.assistant.indicators import Candle, calculate_sma

__all__ = ["calculate_stochastic", "fibonacci_levels", "fibonacci_keys"]


def calculate_stochastic(
    candles: Sequence[Candle],
    k_period: int = 14,
    d_period: int = 3,
    smooth: int = 3,
) -> tuple[float | None, float | None, list[float | None], list[float | None]]:
    """Determine %K and %D oscillator values together with their series.

    Returns (latest %K, latest %D, k_series, d_series). Series are padded with
    None so they stay aligned to the candle list.
    """
    if not candles:
        return None, None, [], []
    closes = [c.close for c in candles]
    raw_k: list[float | None] = []
    for i, candle in enumerate(candles):
        if i < k_period - 1:
            raw_k.append(None)
            continue
        window = candles[i - k_period + 1 : i + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        span = highest - lowest
        if span == 0:
            raw_k.append(100.0)
        else:
            raw_k.append((closes[i] - lowest) / span * 100.0)

    k_series: list[float | None] = [None] * len(raw_k)
    valid: list[float] = [v for v in raw_k if v is not None]
    if not valid:
        return None, None, raw_k, [None] * len(raw_k)

    if smooth and smooth > 1:
        smoothed = calculate_sma(valid, smooth)
        idx = 0
        for i, v in enumerate(raw_k):
            if v is None:
                continue
            k_series[i] = smoothed[idx] if idx < len(smoothed) else v
            idx += 1
    else:
        k_series = raw_k

    d_series: list[float | None] = [None] * len(k_series)
    k_valid = [v for v in k_series if v is not None]
    if len(k_valid) >= d_period:
        d_values = calculate_sma(k_valid, d_period)
        idx = 0
        for i, v in enumerate(k_series):
            if v is None:
                continue
            if idx < len(d_values):
                d_series[i] = d_values[idx]
            idx += 1

    latest_k = next((v for v in reversed(k_series) if v is not None), None)
    latest_d = next((v for v in reversed(d_series) if v is not None), None)
    return (
        round(latest_k, 2) if latest_k is not None else None,
        round(latest_d, 2) if latest_d is not None else None,
        k_series,
        d_series,
    )


FIBONACCI_KEYS = ("0", "0.236", "0.382", "0.5", "0.618", "0.786", "1")


def fibonacci_levels(
    candles: Sequence[Candle], lookback: int = 30
) -> dict[str, float]:
    """Fibonacci retracement levels between the swing high and low of the window.

    Level keys run 0 (extreme high) -> 1 (extreme low). Returns an empty dict
    when there is no valid range to work with.
    """
    if not candles:
        return {}
    window = candles[-lookback:] if len(candles) >= lookback else candles
    swing_high = max(c.high for c in window)
    swing_low = min(c.low for c in window)
    diff = swing_high - swing_low
    if diff <= 0:
        return {}
    return {
        key: round(swing_high - float(key) * diff, 4) for key in FIBONACCI_KEYS
    }