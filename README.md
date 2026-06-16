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
