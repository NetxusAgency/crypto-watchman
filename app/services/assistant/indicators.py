import math
from dataclasses import asdict, dataclass
from typing import Sequence


@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class TechnicalSnapshot:
    symbol: str
    timeframe: str
    current_price: float
    ema_20: float | None
    ema_50: float | None
    sma_200: float | None
    rsi_14: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_hist: float | None
    atr_14: float | None
    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    support_levels: list[float]
    resistance_levels: list[float]
    trend_bias: str

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_sma(values: Sequence[float], period: int) -> list[float | None]:
    """Calculate Simple Moving Average."""
    if period <= 0:
        return [None] * len(values)
    result: list[float | None] = []
    window_sum = 0.0
    for i, val in enumerate(values):
        window_sum += val
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            result.append(round(window_sum / period, 6))
        else:
            result.append(None)
    return result


def calculate_ema(values: Sequence[float], period: int) -> list[float | None]:
    """Calculate Exponential Moving Average."""
    if len(values) < period or period <= 0:
        return [None] * len(values)
    result: list[float | None] = [None] * (period - 1)
    # Seed with SMA of first `period` items
    sma_seed = sum(values[:period]) / period
    result.append(round(sma_seed, 6))

    k = 2.0 / (period + 1)
    current_ema = sma_seed
    for val in values[period:]:
        current_ema = (val * k) + (current_ema * (1.0 - k))
        result.append(round(current_ema, 6))
    return result


def calculate_rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Calculate Relative Strength Index (RSI) for the latest candle."""
    if len(closes) <= period or period <= 0:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    if len(gains) < period:
        return None

    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed averages (Wilder's Smoothing)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def calculate_macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float | None, float | None, float | None]:
    """Calculate MACD Line, Signal Line, and Histogram for the latest candle."""
    if len(closes) < slow + signal:
        return None, None, None

    fast_ema = calculate_ema(closes, fast)
    slow_ema = calculate_ema(closes, slow)

    # MACD line = fast_ema - slow_ema
    macd_series: list[float] = []
    for f, s in zip(fast_ema, slow_ema):
        if f is not None and s is not None:
            macd_series.append(f - s)

    if len(macd_series) < signal:
        return None, None, None

    signal_ema = calculate_ema(macd_series, signal)

    latest_macd = macd_series[-1]
    latest_signal = signal_ema[-1]
    if latest_signal is None:
        return round(latest_macd, 6), None, None

    latest_hist = latest_macd - latest_signal
    return round(latest_macd, 6), round(latest_signal, 6), round(latest_hist, 6)


def calculate_atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    """Calculate Average True Range (ATR) for the latest candle."""
    if len(candles) <= period or period <= 0:
        return None

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        current = candles[i]
        prev = candles[i - 1]
        tr = max(
            current.high - current.low,
            abs(current.high - prev.close),
            abs(current.low - prev.close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # Initial ATR (SMA of first `period` TRs)
    current_atr = sum(true_ranges[:period]) / period

    # Wilder's smoothing
    for tr in true_ranges[period:]:
        current_atr = (current_atr * (period - 1) + tr) / period

    return round(current_atr, 6)


def calculate_bollinger_bands(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> tuple[float | None, float | None, float | None]:
    """Calculate Bollinger Bands (Upper, Middle, Lower)."""
    if len(closes) < period or period <= 0:
        return None, None, None

    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std_dev = math.sqrt(variance)

    upper = middle + (num_std * std_dev)
    lower = middle - (num_std * std_dev)
    return round(upper, 6), round(middle, 6), round(lower, 6)


def calculate_support_resistance(candles: Sequence[Candle], lookback: int = 30) -> tuple[list[float], list[float]]:
    """
    Detect key support and resistance levels using swing highs and swing lows.
    Returns sorted ([supports], [resistances]) relative to latest close price.
    """
    if not candles:
        return [], []

    latest_close = candles[-1].close
    window = candles[-lookback:] if len(candles) >= lookback else candles

    highs: list[float] = []
    lows: list[float] = []

    # Swing pivots: a candle whose high/low is higher/lower than immediate neighbors
    for i in range(1, len(window) - 1):
        if window[i].high > window[i - 1].high and window[i].high > window[i + 1].high:
            highs.append(window[i].high)
        if window[i].low < window[i - 1].low and window[i].low < window[i + 1].low:
            lows.append(window[i].low)

    # Fallback to local min/max if no swing detected
    if not highs:
        highs = [max(c.high for c in window)]
    if not lows:
        lows = [min(c.low for c in window)]

    # Filter into supports (below current price) and resistances (above current price)
    supports = sorted(list({round(p, 4) for p in lows if p < latest_close}), reverse=True)[:3]
    resistances = sorted(list({round(p, 4) for p in highs if p > latest_close}))[:3]

    # If empty, extrapolate from range
    if not supports:
        supports = [round(latest_close * 0.97, 4)]
    if not resistances:
        resistances = [round(latest_close * 1.03, 4)]

    return supports, resistances


def compute_all_indicators(symbol: str, timeframe: str, candles: Sequence[Candle]) -> TechnicalSnapshot:
    """Compute complete technical snapshot from candles."""
    if not candles:
        return TechnicalSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            current_price=0.0,
            ema_20=None,
            ema_50=None,
            sma_200=None,
            rsi_14=None,
            macd_line=None,
            macd_signal=None,
            macd_hist=None,
            atr_14=None,
            bb_upper=None,
            bb_middle=None,
            bb_lower=None,
            support_levels=[],
            resistance_levels=[],
            trend_bias="NEUTRAL",
        )

    closes = [c.close for c in candles]
    current_price = round(closes[-1], 6)

    ema20_series = calculate_ema(closes, 20)
    ema50_series = calculate_ema(closes, 50)
    sma200_series = calculate_sma(closes, 200)

    ema_20 = ema20_series[-1] if ema20_series else None
    ema_50 = ema50_series[-1] if ema50_series else None
    sma_200 = sma200_series[-1] if sma200_series else None

    rsi_14 = calculate_rsi(closes, 14)
    macd_line, macd_signal, macd_hist = calculate_macd(closes, 12, 26, 9)
    atr_14 = calculate_atr(candles, 14)
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(closes, 20, 2.0)
    supports, resistances = calculate_support_resistance(candles)

    # Determine trend bias deterministically
    bullish_votes = 0
    bearish_votes = 0

    if ema_20 and ema_50:
        if ema_20 > ema_50:
            bullish_votes += 1
        elif ema_20 < ema_50:
            bearish_votes += 1

    if ema_20:
        if current_price > ema_20:
            bullish_votes += 1
        elif current_price < ema_20:
            bearish_votes += 1

    if rsi_14 is not None:
        if rsi_14 > 55:
            bullish_votes += 1
        elif rsi_14 < 45:
            bearish_votes += 1

    if macd_hist is not None:
        if macd_hist > 0:
            bullish_votes += 1
        elif macd_hist < 0:
            bearish_votes += 1

    if bullish_votes >= 3 and bullish_votes > bearish_votes:
        trend_bias = "BULLISH"
    elif bearish_votes >= 3 and bearish_votes > bullish_votes:
        trend_bias = "BEARISH"
    else:
        trend_bias = "NEUTRAL"

    return TechnicalSnapshot(
        symbol=symbol.upper(),
        timeframe=timeframe,
        current_price=current_price,
        ema_20=ema_20,
        ema_50=ema_50,
        sma_200=sma_200,
        rsi_14=rsi_14,
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        atr_14=atr_14,
        bb_upper=bb_upper,
        bb_middle=bb_middle,
        bb_lower=bb_lower,
        support_levels=supports,
        resistance_levels=resistances,
        trend_bias=trend_bias,
    )
