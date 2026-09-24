# forex-bot

A risk-managed FX trading system for MetaTrader 5: a research pipeline for testing
strategy ideas honestly, and a paper-trading executor with an LLM news-risk veto,
Telegram control and independent monitoring.

> **Status:** Three structured research studies found **no tradable edge** after costs.
> The executor therefore runs in `dry_run` (paper trading) only. This is not financial advice.

## Highlights

- **End-to-end pipeline:** MT5 data ingestion, validation, feature engineering,
  triple-barrier labelling, walk-forward training, backtesting
- **Safety-first execution:** hard risk caps in code, latching kill switch,
  daily/weekly loss limits, Friday flattening, restart-safe position tracking
- **LLM news-risk veto:** Claude (Haiku + web search) can only allow or block a trade;
  answers are validated as JSON, and any API error or invalid answer blocks the trade
- **Remote control and monitoring:** Telegram alerts and commands (whitelisted chat),
  plus a separate-process watchdog via Windows Task Scheduler
- **Honest research:** pre-registered hypotheses, documented amendments, and
  negative results kept on record
- **69 automated tests**, including no-lookahead checks for features and labels

## Architecture

```
MT5 terminal ──> mt5_client ──> data_checks ──> features ──> strategy ──> Signal
                                                                            │
                                                  risk (sizing + rules) <───┘
                                                            │
                                         news veto (Claude + web search)
                                                            │
Telegram <──> notifier <──> executor <──────────────────────┘
                              │
                 ┌────────────┼──────────────┐
                 ▼            ▼              ▼
          paper broker     SQLite log     state (equity, kill switch,
          (simulated       (decisions,     pause flag, heartbeat)
           fills)           trades)            │
                                               ▼
                                     watchdog (Task Scheduler, every 10 min)
```

Each H4 bar close: validate data → check paper SL/TP → update equity → kill switch /
pause checks → features → strategy signal → risk checks → news veto (Claude) →
paper order → log + notify.

## Safety design

| Risk | Mitigation |
|---|---|
| Config typo sets risk too high | Pydantic validation + hard caps in code (max 2% per trade) |
| Position too large for the account | Lots always rounded **down**; trade skipped if min lot exceeds risk |
| Losing streak | Daily (2%) and weekly (4%) loss limits |
| Large drawdown | Kill switch at 15% from peak; **latches** across restarts until manual reset |
| Duplicate positions after restart | Positions identified by magic number and synced from MT5 on startup |
| Weekend gaps | All positions flattened before the Friday close |
| Trading into high-impact news | Claude veto with web search; API errors or invalid answers block the trade (fail-safe) |
| LLM overriding the strategy | The veto can only allow or block; it never sets direction, size, SL or TP |
| Stale or malicious remote commands | Telegram commands only from one chat ID; pending updates skipped at startup |
| Bot crashes silently | Heartbeat file + separate watchdog process with Telegram alerts |
| Accidental live trading | Executor refuses any mode except `dry_run`; research used a read-only investor login |

## Research findings

| Study | Data | Result |
|---|---|---|
| LightGBM on EURUSD H4 (18 features, triple-barrier labels, walk-forward) | Broker MT5 | **No edge.** Apparent AUC 0.65–0.75 came from an artifact in the broker's pre-2024 data feed |
| EURUSD D1 time-series momentum (pre-registered) | Dukascopy | **FAIL.** Sign of results flipped between in-sample and out-of-sample |
| 10-pair equal-risk momentum portfolio (pre-registered) | Broker MT5, validated against Dukascopy | **FAIL.** Out-of-sample Sharpe −0.60 / −0.54 / −0.13 |

Key lessons, documented in `research/`:

- **Broker history can change feeds.** Median spread jumped from 0 to 13 points in July 2024;
  a model trained across the switch learned short-term patterns that only existed in the old feed.
- **Walk-forward plus diagnostics caught it**, whereas a pooled backtest would have shown
  a 57–60% win rate.
- **Pre-registration prevents data snooping:** parameters and pass/fail criteria were fixed
  before running, and bugs were fixed only per the original spec.
- **Downloaders fail silently.** Rate-limited requests produced year-long holes; explicit
  gap checks caught them before they affected results.
- **LLM instructions need precise scope.** The first veto prompt blocked on non-USD/EUR
  central banks and medium-impact data; an explicit event list fixed it.

## Project structure

```
src/
  config_schema.py   Pydantic config + hard risk caps
  mt5_client.py      MT5 wrapper: connect, chunked history, ticks, positions
  timeutils.py       Broker server time <-> UTC, bar close, Friday cutoff, market hours
  data_checks.py     Duplicates, invalid OHLC, weekend junk, unclosed bars, gaps
  storage.py         CSV bar storage
  features.py        Feature engineering (no-lookahead tested)
  labeling.py        Triple-barrier labels with conservative fill rules
  mlutils.py         ROC AUC + isotonic calibration in numpy
  train.py           Purged walk-forward training (LightGBM native API)
  strategy.py        Strategy interface + placeholder rule-based strategy
  risk.py            Position sizing + pre-trade checks
  state.py           Equity tracking, kill switch, control flags, heartbeat
  db.py              SQLite log of decisions, trades, events
  paper.py           Paper broker with realistic simulated fills
  notifier.py        Telegram alerts + whitelisted commands
  veto.py            Claude news-risk veto (allow/block only, fail-safe)
  executor.py        Main loop (dry_run only)
  watchdog.py        Separate-process health monitor
scripts/             Data fetching, dataset building, training, research, diagnostics
research/            Pre-registrations, amendments and results
tests/               69 pytest tests
```

## Setup (Windows)

Requirements: 64-bit Python 3.11+, MetaTrader 5 terminal with algo trading enabled.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create `.env` (never commit it):

```text
MT5_LOGIN=...
MT5_PASSWORD=...          # investor (read-only) password is enough for dry_run
MT5_SERVER=...
LIVE_TRADING_CONFIRMED=NO
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ANTHROPIC_API_KEY=...     # optional: enables the news veto
```

Adjust `config.yaml` (symbol, timeframe, risk limits, MT5 terminal path).

## Usage

```powershell
python -m pytest tests -q                              # run all tests
python -m scripts.check_connection                     # verify MT5 access
python -m scripts.fetch_data                           # download H4 history
python -m scripts.build_dataset                        # features + labels
python -m scripts.run_training                         # walk-forward evaluation
python -m scripts.test_veto_live                       # one real veto call (costs a few cents)
python -m src.executor --paper-equity 1100 --once      # process the latest bar
python -m src.executor --paper-equity 1100             # run continuously
python -m src.watchdog                                 # one health check
```

Telegram commands: `/status`, `/pause`, `/resume`, `/closeall`, `/help`.

## Environment notes

Windows Smart App Control blocked some compiled dependencies (pyarrow's parquet module,
parts of scikit-learn). The project avoids them instead of disabling OS security:
CSV storage, ROC AUC plus isotonic calibration implemented in numpy, and the Claude and
Telegram APIs called with the standard library instead of SDKs.

## Disclaimer

For education and research only. Trading leveraged FX carries a high risk of loss.
No strategy in this repository has demonstrated an edge after costs.