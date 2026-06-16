# Backtest engine (deploy bundle)

Minimal subset of the TopStep bot stack required to run the Streamlit backtester on [Streamlit Cloud](https://share.streamlit.io).

## Layout

| Package | Role |
|---------|------|
| `core/` | `TrendMomentumStrategy`, bars, indicators, risk/session clock, backtest types |
| `broker/` | `PaperBroker` — next-bar-open fills, stops, targets, trailing stop manager |
| `infra/` | `AppConfig` / `config.yaml` loading |

`app.py` adds this folder to `sys.path` so imports like `from core.strategy import …` resolve here.

## Source of truth

`core/strategy.py` mirrors the client’s `strategy.py` at the repo root. The live bot’s full codebase stays in `TopStepBot_source/` (local only, gitignored).
