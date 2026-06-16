"""Config and secrets. AppConfig is a Pydantic model loaded from config.yaml.
Secrets (username, API key) come from .env or the OS keyring, never the YAML.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from infra.paths import app_path

# Contract specifications
class ContractSpec(BaseModel):
    """Static per-instrument trading parameters."""
    symbol: str
    tick_size: float          # smallest price increment
    tick_value: float         # $ per tick per contract
    description: str = ""


CONTRACT_SPECS: dict[str, ContractSpec] = {
    "MNQ": ContractSpec(symbol="MNQ", tick_size=0.25, tick_value=0.50,
                        description="Micro E-mini Nasdaq-100"),
    "NQ": ContractSpec(symbol="NQ", tick_size=0.25, tick_value=5.00,
                       description="E-mini Nasdaq-100"),
}


def contract_spec(symbol: str) -> ContractSpec:
    try:
        return CONTRACT_SPECS[symbol.upper()]
    except KeyError as exc:  # pragma: no cover
        raise ValueError(f"Unknown instrument {symbol!r}; known: "
                         f"{', '.join(CONTRACT_SPECS)}") from exc


# Config sub-models
class TradingWindow(BaseModel):
    start_et: str = "09:30"
    end_et: str = "16:00"


class MarketConfig(BaseModel):
    instrument: Literal["MNQ", "NQ"] = "MNQ"
    timeframe_minutes: int = Field(5, ge=1, le=240)
    vwap_anchor: Literal["rth", "globex"] = "rth"
    trading_window: TradingWindow = TradingWindow()
    flatten_et: str = "15:55"

    # When on, the session clock uses extended_window/extended_flatten_et
    # instead of the RTH window. The window may wrap past midnight (start > end).
    extended_hours: bool = False
    extended_window: TradingWindow = TradingWindow(start_et="18:00", end_et="17:00")
    extended_flatten_et: str = "16:59"

    @property
    def spec(self) -> ContractSpec:
        return contract_spec(self.instrument)


class StrategyConfig(BaseModel):
    """Trend + momentum-continuation parameters. Trend is the EMA_fast vs
    EMA_slow relationship; entries fire on momentum candles and/or breaks of the
    previous candle's extreme. ATR drives the strong-candle/spike/chop guards.
    """
    # trend
    ema_fast: int = Field(20, ge=1)
    ema_slow: int = Field(50, ge=2)
    require_vwap: bool = False           # also require close vs VWAP for trend
    atr_period: int = Field(14, ge=1)

    # entry triggers
    entry_on_momentum_candle: bool = True   # strong continuation candle
    entry_on_prev_break: bool = True        # break of previous candle high/low
    strong_candle_body_ratio: float = Field(0.5, ge=0, le=1)   # body/range
    strong_candle_atr_mult: float = Field(0.8, ge=0)           # range >= mult*ATR

    # avoidance guards
    spike_atr_mult: float = Field(2.0, gt=0)        # range > mult*ATR = spike
    chop_ema_atr_mult: float = Field(0.25, ge=0)    # |EMAΔ| < mult*ATR = chop
    no_entry_minutes_before_close: int = Field(15, ge=0)

    # stop / target
    # risk_mode picks ONE bracket scheme (mutually exclusive):
    #   dynamic - structure stop (stop_mode + tick_buffer) and target at tp_ratio x risk
    #   fixed   - stop/target a fixed tick distance from the fill (fixed_stop_ticks /
    #             fixed_tp_ticks); stop_mode/stop_swing_lookback/tick_buffer/tp_ratio unused
    risk_mode: Literal["dynamic", "fixed"] = "dynamic"
    stop_mode: Literal["swing", "candle"] = "swing"
    stop_swing_lookback: int = Field(5, ge=1)
    tick_buffer: int = Field(2, ge=0)
    tp_ratio: float = Field(2.0, gt=0)
    fixed_stop_ticks: int = Field(40, ge=1)   # used only when risk_mode == "fixed"
    fixed_tp_ticks: int = Field(60, ge=1)     # used only when risk_mode == "fixed"

    # sides
    enable_long: bool = True
    enable_short: bool = True

    @field_validator("ema_slow")
    @classmethod
    def slow_gt_fast(cls, v: int, info):  # noqa: ANN001
        fast = info.data.get("ema_fast", 20)
        if v <= fast:
            raise ValueError("ema_slow must be greater than ema_fast")
        return v


class RiskConfig(BaseModel):
    contracts_per_trade: int = Field(1, ge=1)
    max_trades_per_day: int = Field(10, ge=1)
    daily_loss_limit_currency: float = Field(1000.0, ge=0)
    daily_profit_limit_currency: float = Field(0.0, ge=0)  # 0 disables the target
    max_consecutive_losses: int = Field(3, ge=1)
    account_type: Literal["combine", "express", "live_funded"] = "live_funded"
    trailing_drawdown_currency: float = Field(2000.0, ge=0)
    trailing_drawdown_guard_enabled: bool = True
    trailing_drawdown_buffer: float = Field(200.0, ge=0)
    flatten_on_disconnect: bool = True


class ExecutionConfig(BaseModel):
    entry_fill_model: Literal["next_open", "signal_close"] = "next_open"
    slippage_ticks: int = Field(1, ge=0)
    commission_per_side: float = Field(0.0, ge=0)

    # Live order placement (real money). Real orders are sent only when
    # mode == "live" AND allow_live_orders is True; otherwise the live broker
    # stays read-only and fails safe, so selecting LIVE alone never trades.
    allow_live_orders: bool = False
    live_entry_order_type: Literal["market", "limit"] = "market"
    # Hard per-order contract ceiling, backstop against a misconfigured
    # contracts_per_trade sending an oversized live order.
    max_live_contracts: int = Field(10, ge=1)


class ExitConfig(BaseModel):
    """Two-stage stop manager (single toggle), profit-based. Stage 1: once price
    travels breakeven_trigger_pct of the way from entry to target, the stop jumps
    to break-even (plus a small fee buffer). Stage 2: after that the stop trails
    to lock trail_lock_pct of peak open profit, ratcheting tighter only, and
    exits the full position when hit.
    """
    trailing_stop_enabled: bool = False
    breakeven_trigger_pct: float = Field(10.0, gt=0, le=100)  # % of entry->target
    trail_lock_pct: float = Field(50.0, gt=0, le=100)         # % of peak profit to lock


class TelegramConfig(BaseModel):
    """Telegram trade-alert settings. bot_token/chat_id are stored in config.yaml
    in plaintext, so keep that file private. The token is never logged.
    """
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    notify_entry: bool = True
    notify_exit: bool = True


class AccountConfig(BaseModel):
    """Which trading account the bot uses. Empty account_id means the engine
    picks the first active account the gateway returns. account_id is the
    authoritative selector; account_name is a display label and fallback match.
    A set selection that matches no active account aborts the connection rather
    than trading a different account.
    """
    account_id: str = ""
    account_name: str = ""


class AppConfig(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    account: AccountConfig = AccountConfig()
    market: MarketConfig = MarketConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()
    exits: ExitConfig = ExitConfig()
    telegram: TelegramConfig = TelegramConfig()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        path = Path(path) if path is not None else app_path("config.yaml")
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.model_validate(raw)

    def save(self, path: str | Path | None = None) -> None:
        path = Path(path) if path is not None else app_path("config.yaml")
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.model_dump(mode="json"), fh,
                           sort_keys=False, default_flow_style=False)


# Secrets
class Secrets(BaseModel):
    username: str = ""
    api_key: str = ""
    api_base: str = "https://api.topstepx.com"
    rtc_base: str = "https://rtc.topstepx.com"

    @property
    def is_complete(self) -> bool:
        return bool(self.username and self.api_key)


_KEYRING_SERVICE = "topstep_bot"


def load_secrets(env_path: str | Path | None = None) -> Secrets:
    """Load credentials, preferring .env then falling back to the keyring.
    .env is resolved next to the executable (or project root in dev). Keyring
    reads are best-effort and degrade to whatever .env provided.
    """
    env_path = Path(env_path) if env_path is not None else app_path(".env")
    # Load .env without clobbering real env vars.
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:  # pragma: no cover
        pass

    username = os.getenv("TOPSTEPX_USERNAME", "")
    api_key = os.getenv("TOPSTEPX_API_KEY", "")

    if not api_key:
        api_key = _keyring_get("api_key")
    if not username:
        username = _keyring_get("username")

    return Secrets(
        username=username,
        api_key=api_key,
        api_base=os.getenv("PROJECTX_API_BASE", "https://api.topstepx.com"),
        rtc_base=os.getenv("PROJECTX_RTC_BASE", "https://rtc.topstepx.com"),
    )


def _keyring_get(name: str) -> str:
    try:
        import keyring

        return keyring.get_password(_KEYRING_SERVICE, name) or ""
    except Exception:  # pragma: no cover
        return ""


def store_secret_in_keyring(name: str, value: str) -> bool:
    """Persist a secret to the OS keyring. Returns False if unavailable."""
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, name, value)
        return True
    except Exception:  # pragma: no cover
        return False
