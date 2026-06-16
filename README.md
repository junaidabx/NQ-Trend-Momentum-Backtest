# NQ Trend-Momentum Backtest

Streamlit backtester for the client **TrendMomentumStrategy** on **NQ 1-minute** CSV data.

## Quick start (local)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
streamlit run app.py
```

Put TradingView CSVs in `NQ Data/` or `data/` (gitignored — upload in the app on Cloud), or use **Upload CSV** in the UI.

## Repo layout

| Path | Purpose |
|------|---------|
| `app.py` | Streamlit entry point |
| `backtest_app/` | Data loader, runner, charts, UI |
| `engine/` | **Deployable** strategy + paper broker + config (see `engine/README.md`) |
| `config.yaml` | Default strategy / risk settings |
| `strategy.py` | Client reference copy (logic lives in `engine/core/strategy.py`) |

`TopStepBot_source/` is the full live bot (local only, not pushed to GitHub).

## Deploy to Streamlit Cloud

Repo: [github.com/junaidabx/NQ-Trend-Momentum-Backtest](https://github.com/junaidabx/NQ-Trend-Momentum-Backtest)

### 1. Push code (first time)

```bash
cd c:\Users\User\Python_Drive\Scraping_projects\NQ-backtest-strategy

git add .
git commit -m "Initial Streamlit backtester with engine bundle"
git branch -M main
git remote add origin https://github.com/junaidabx/NQ-Trend-Momentum-Backtest.git
git push -u origin main
```

If `origin` already exists: `git remote set-url origin https://github.com/junaidabx/NQ-Trend-Momentum-Backtest.git`

### 2. Create the Cloud app

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **New app** → pick **junaidabx/NQ-Trend-Momentum-Backtest**.
3. **Main file path:** `app.py`
4. **Branch:** `main`
5. Deploy.

### 3. Data on Cloud

Bundled CSV folders are gitignored (large files). Either:

- Use **Upload CSV** in the app after deploy, or  
- Commit a small sample CSV under `data/` and remove that path from `.gitignore`, or  
- Use Git LFS for `NQ Data/`.

## Contract

NQ: **$20/point**, **$5/tick** (0.25 pt tick size)
 
## How the strategy works (summary)

This project implements the client's Trend‑Momentum strategy (reference logic in `strategy.py`, production logic in `engine/core/strategy.py`). Key rules:

- Trend is determined by two EMAs (fast vs slow). Only trades aligned with the trend are considered.
- Entries are triggered by either a strong momentum candle (body vs range + ATR multiple) or a break of the previous candle's extreme.
- ATR-based guards: strong‑candle requirement, spike rejection, and chop checks (EMA separation vs ATR).
- Optional VWAP filter: require close above/below session VWAP for longs/shorts when enabled.
- Risk & exits:
  - Two risk modes: dynamic (stop at swing/candle + R-multiple target) or fixed (tick-based stop & TP).
  - Trailing stops supported; slippage and tick buffer applied to simulated fills.
- Session handling:
  - Near‑close blocking and flatten rules mirror live behavior (configurable flatten time).
  - Fills are simulated at the next bar open (consistent with the live bot's fill model).

See `config.yaml` for default parameter values (EMA periods, ATR period, stop/TP, spike/chop thresholds).

## How the backtest works

- Data ingestion:
  - CSVs are TradingView export format with a Unix UTC `time` column. The app accepts multiple uploaded CSVs — they are merged and sorted by timestamp regardless of upload order.
  - Bundled CSVs under `data/` or `NQ Data/` are discovered and concatenated when selected.
- Timezones & warmup:
  - All bar timestamps are converted to America/New_York (ET) for session/window calculations and display.
  - A warmup window (based on EMA/ATR lookbacks) is prepended so indicators are fully initialized before the test window.
- Resampling and slicing:
  - Source CSV timeframe is inferred automatically; data is optionally resampled to the strategy's target timeframe.
  - The user selects a history start/end (end capped at the last CSV bar). `slice_for_backtest()` builds warmup + test slices and validates enough warmup bars are available.
- Simulation engine:
  - The engine (`engine/core/backtest.py`, `engine/broker/paper_broker.py`) replays bars, evaluates signals on bar close, and simulates fills at next bar open with configured slippage.
  - Position sizing, stops, TP, trailing, and rejection logic are applied exactly as in the strategy implementation.
- Output:
  - The app shows equity, drawdown, trades, and a Plotly price chart with entry/exit markers and lightweight pan buffer for performance.
  - Exports: HTML report, trades CSV, ZIP bundle with run config.

## Notes & tips

- For Streamlit Cloud set Python to 3.12 in the app settings (3.14 may cause runtime import/watch issues).
- Use the **Upload CSV** control to provide data on Cloud; the app supports multiple files and will merge them automatically.