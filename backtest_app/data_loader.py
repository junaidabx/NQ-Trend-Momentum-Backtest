"""Load TradingView-style CSV OHLC into Bar objects with optional resampling."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Optional, Union

import pandas as pd
from dateutil.relativedelta import relativedelta

from core.bars import Bar, floor_to_interval

PathLike = Union[str, Path]
FileSource = Union[PathLike, BinaryIO, io.BytesIO]


@dataclass(frozen=True)
class DataSlice:
    """Warmup + backtest windows derived from loaded history."""

    all_bars: list[Bar]
    warmup_bars: list[Bar]
    test_bars: list[Bar]
    end: datetime
    start: datetime
    source_timeframe_minutes: int
    target_timeframe_minutes: int
    requested_start: datetime
    requested_end: datetime | None = None
    clamped_to_data: bool = False

    @property
    def warmup_count(self) -> int:
        return len(self.warmup_bars)

    @property
    def test_count(self) -> int:
        return len(self.test_bars)


def discover_csv_paths(*roots: Path) -> list[Path]:
    """Return sorted CSV paths from the first existing root(s)."""
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.csv")):
            key = path.name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out


def _read_csv(source: FileSource) -> pd.DataFrame:
    df = pd.read_csv(source)
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise ValueError(f"CSV missing columns: {', '.join(sorted(missing))}")
    df = df.rename(columns={c: c.lower() for c in df.columns})
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df.sort_values("time").drop_duplicates("time", keep="last")
    return df


def _infer_source_minutes(df: pd.DataFrame) -> int:
    if len(df) < 2:
        return 1
    diffs = df["time"].diff().dropna()
    median = int(diffs.median())
    if median <= 0:
        return 1
    return max(1, median // 60)


def _df_to_bars(df: pd.DataFrame, timeframe_minutes: int) -> list[Bar]:
    bars: list[Bar] = []
    for row in df.itertuples(index=False):
        start = datetime.fromtimestamp(int(row.time), tz=timezone.utc)
        bar = Bar(
            start=start,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(getattr(row, "volume", 0.0) or 0.0),
        )
        bar._end = start + timedelta(minutes=timeframe_minutes)  # type: ignore[attr-defined]
        bars.append(bar)
    return bars


def resample_bars(bars: list[Bar], target_minutes: int) -> list[Bar]:
    """Aggregate bars to a higher timeframe aligned to wall-clock buckets."""
    if not bars or target_minutes <= 0:
        return bars
    out: list[Bar] = []
    bucket_start: Optional[datetime] = None
    o = h = l = c = 0.0
    vol = 0.0

    def flush(start: datetime) -> None:
        nonlocal o, h, l, c, vol
        bar = Bar(start, o, h, l, c, vol)
        bar._end = start + timedelta(minutes=target_minutes)  # type: ignore[attr-defined]
        out.append(bar)

    for bar in bars:
        start = floor_to_interval(bar.start, target_minutes)
        if bucket_start is None:
            bucket_start = start
            o, h, l, c, vol = bar.open, bar.high, bar.low, bar.close, bar.volume
            continue
        if start > bucket_start:
            flush(bucket_start)
            bucket_start = start
            o, h, l, c, vol = bar.open, bar.high, bar.low, bar.close, bar.volume
        else:
            h = max(h, bar.high)
            l = min(l, bar.low)
            c = bar.close
            vol += bar.volume
    if bucket_start is not None:
        flush(bucket_start)
    return out


def load_bars_from_csv(
    source: FileSource,
    target_timeframe_minutes: int = 5,
    source_timeframe_minutes: Optional[int] = None,
) -> tuple[list[Bar], int, int]:
    """Parse CSV and optionally resample to the strategy timeframe."""
    df = _read_csv(source)
    src_tf = source_timeframe_minutes or _infer_source_minutes(df)
    bars = _df_to_bars(df, src_tf)
    if target_timeframe_minutes != src_tf:
        bars = resample_bars(bars, target_timeframe_minutes)
    return bars, src_tf, target_timeframe_minutes


def load_bars_from_paths(
    paths: list[Path],
    target_timeframe_minutes: int = 5,
) -> tuple[list[Bar], int, int]:
    """Load and concatenate multiple CSV files in chronological order."""
    if not paths:
        raise ValueError("No CSV files selected")
    frames = [_read_csv(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("time").drop_duplicates("time", keep="last")
    src_tf = _infer_source_minutes(df)
    bars = _df_to_bars(df, src_tf)
    if target_timeframe_minutes != src_tf:
        bars = resample_bars(bars, target_timeframe_minutes)
    return bars, src_tf, target_timeframe_minutes


def required_warmup_bars(
    ema_slow: int,
    atr_period: int,
    stop_swing_lookback: int,
    buffer: int = 20,
) -> int:
    """Minimum startup bars so EMA / ATR / swing stop are fully warm."""
    return max(ema_slow, atr_period, stop_swing_lookback) + buffer


def data_bounds(bars: list[Bar]) -> tuple[datetime, datetime]:
    """Return (first_bar_utc, last_bar_utc)."""
    if not bars:
        raise ValueError("No bars loaded")
    return bars[0].start, bars[-1].start


def default_window_end_start(
    bars: list[Bar],
    months: int = 3,
) -> tuple[datetime, datetime]:
    """Default backtest end = latest bar; start = ``months`` calendar months earlier."""
    end = bars[-1].start
    start = end - relativedelta(months=months)
    return end, start


def earliest_test_start(bars: list[Bar], min_warmup: int) -> datetime:
    """First UTC bar time that has ``min_warmup`` startup bars before it."""
    if len(bars) <= min_warmup:
        raise ValueError(
            f"Need at least {min_warmup + 1} bars for warmup; have {len(bars)}."
        )
    return bars[min_warmup].start


def slice_for_backtest(
    bars: list[Bar],
    *,
    months: int = 3,
    min_warmup: int,
    end: Optional[datetime] = None,
    start: Optional[datetime] = None,
    source_timeframe_minutes: int = 1,
    target_timeframe_minutes: int = 5,
) -> DataSlice:
    """Split history into warmup (startup candles) and the backtest window."""
    if not bars:
        raise ValueError("No bars loaded")

    default_end, default_start = default_window_end_start(bars, months=months)
    end_dt = end or default_end
    requested_end = end_dt
    requested_start = start or default_start
    start_dt = requested_start
    clamped = False

    idx_end = len(bars) - 1
    while idx_end >= 0 and bars[idx_end].start > end_dt:
        idx_end -= 1
    if idx_end < 0:
        raise ValueError("No bars on or before the selected end time")

    end_dt = bars[idx_end].start

    if end_dt < requested_start:
        raise ValueError(
            f"End time {end_dt.isoformat()} is before start time {requested_start.isoformat()}"
        )

    test_start_idx = 0
    while test_start_idx < len(bars) and bars[test_start_idx].start < start_dt:
        test_start_idx += 1

    if test_start_idx <= idx_end and bars[test_start_idx].start > requested_start:
        clamped = True

    warmup_start_idx = max(0, test_start_idx - min_warmup)
    if test_start_idx - warmup_start_idx < min_warmup:
        test_start_idx = min_warmup
        warmup_start_idx = 0
        clamped = True

    if test_start_idx > idx_end:
        raise ValueError("Backtest start is after the selected end time")

    warmup = bars[warmup_start_idx:test_start_idx]
    test = bars[test_start_idx: idx_end + 1]

    if len(warmup) < min_warmup:
        raise ValueError(
            f"Not enough warmup bars: have {len(warmup)}, need {min_warmup}. "
            "Move the start date later or load earlier CSV data."
        )
    if not test:
        raise ValueError("Backtest window is empty for the selected date range")

    return DataSlice(
        all_bars=bars,
        warmup_bars=warmup,
        test_bars=test,
        end=end_dt,
        start=test[0].start,
        source_timeframe_minutes=source_timeframe_minutes,
        target_timeframe_minutes=target_timeframe_minutes,
        requested_start=requested_start,
        requested_end=requested_end,
        clamped_to_data=clamped,
    )
