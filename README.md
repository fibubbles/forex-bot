# forex-bot

A risk-managed FX trading system for MetaTrader 5: a research pipeline for testing
strategy ideas honestly, and an executor that paper-trades or places real orders on a
**demo** account, with an LLM news-risk veto, Telegram control and independent monitoring.

> **Status:** Three structured research studies found **no tradable edge** after costs.
> The executor runs in `dry_run` (paper) or `demo` (real orders on a demo account) for
> forward testing only. Live trading is locked. This is not financial advice.

## Highlights

- **End-to-end pipeline:** MT5 data ingestion, validation, feature engineering,
  triple-barrier labelling, walk-forward training, backtesting
- **Real order execution (demo only):** order checks before sending, requote retries,
  broker-side SL/TP, and verification that every position actually has a stop loss
- **Safety-first design:** hard risk caps in code, latching kill switch, daily/weekly
  loss limits, Friday flattening, restart-safe position tracking and orphan adoption
- **LLM news-risk veto:** Claude (Haiku + web search) can only allow or block a trade;
  answers are validated as JSON, and any API error or invalid answer blocks the trade
- **Remote control and monitoring:** Telegram alerts with an offline outbox, whitelisted
  commands, and a separate-process watchdog via Windows Task Scheduler
- **Honest research:** pre-registered hypotheses, documented amendments, and
  negative results kept on record
- **89 automated tests**, including no-lookahead checks and a fake MT5 for order logic

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
                   trading adapter (one interface)
                   ├── PaperTrading  (dry_run: simulated fills)
                   └── LiveTrading   (demo: LiveBroker -> MT5 orders,
                                      exits read from broker deal history)
                              │
                 ┌────────────┼──────────────┐
                 ▼            ▼              ▼
             SQLite log   state (equity,   watchdog (Task Scheduler,
             (decisions,   kill switch,     every 10 min, reads the
              trades)      pause, heartbeat) heartbeat)
```

Each H4 bar close: validate data → check exits → update equity → kill switch / pause
checks → features → strategy signal → risk checks → news veto → order → log + notify.
In demo mode, broker-side SL/TP exits are checked every 10 seconds.

## Safety design

| Risk | Mitigation |
|---|---|
| Real money traded by accident | `live` mode is not implemented; demo mode refuses to start unless the account is a DEMO account on a `*demo*` server |
| Config typo sets risk too high | Pydantic validation + hard caps in code (max 2% per trade) |
| Position too large for the account | Lots always rounded **down**; trade skipped if min lot exceeds risk |
| Broker drops the stop loss | Every order carries SL/TP; a filled position without SL is closed immediately |
| Losing streak | Daily (2%) and weekly (4%) loss limits |
| Large drawdown | Kill switch at 15% from peak; **latches** across restarts until manual reset |
| Crash between fill and logging | Positions with our magic number but unknown to the DB are adopted on startup |
| Duplicate positions after restart | MT5 positions (by magic number) are the source of truth |
| Weekend gaps | All positions flattened before the Friday close |
| Trading into high-impact news | Claude veto with web search; API errors or invalid answers block the trade |
| LLM overriding the strategy | The veto can only allow or block; it never sets direction, size, SL or TP |
| Stale or malicious remote commands | Telegram commands only from one chat ID; pending updates skipped at startup |
| Alerts lost during network outages | Telegram outbox with backoff; queued messages are delivered in order later |
| Bot crashes silently | Heartbeat file + separate watchdog process with Telegram alerts |

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
  broker.py          Real MT5 orders (demo only): checks, retries, SL verification
  trading.py         One interface for paper and live trading
  notifier.py        Telegram alerts (outbox + backoff) and whitelisted commands
  veto.py            Claude news-risk veto (allow/block only, fail-safe)
  net.py             IPv4-only HTTPS opener (stdlib)
  executor.py        Main loop (dry_run or demo)
  watchdog.py        Separate-process health monitor
scripts/             Data, training, research, diagnostics, smoke tests, reports
research/            Pre-registrations, amendments and results
tests/               89 pytest tests
CLAUDE.md            Safety rules and conventions for AI coding assistants
```

## Setup (Windows)

Requirements: 64-bit Python 3.11+, MetaTrader 5 terminal with Algo Trading enabled.

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

For demo mode, create `.env.demo` with the demo account's `MT5_LOGIN`, `MT5_PASSWORD` and
`MT5_SERVER` (it overrides `.env`), and use `config.demo.yaml` (`mode: demo`).

## Usage

```powershell
python -m pytest tests -q                              # run all tests
python -m scripts.check_connection                     # verify MT5 access
python -m scripts.fetch_data                           # download H4 history
python -m scripts.build_dataset                        # features + labels
python -m scripts.run_training                         # walk-forward evaluation
python -m scripts.test_veto_live                       # one real veto call (costs a few cents)
python -m scripts.demo_order_smoke                     # open + verify + close one demo order

# dry_run (paper trading)
python -m src.executor --start-equity 1100

# demo (real orders on a demo account; sizing as if equity were 1100)
python -m src.executor --config config.demo.yaml --env .env.demo --start-equity 1100

python -m scripts.report --days 7 --telegram           # activity summary
python -m src.watchdog                                 # one health check
```

Add `--once` to the executor to process the latest closed bar and exit.
Telegram commands: `/status`, `/pause`, `/resume`, `/closeall`, `/help`.

## Environment notes

- Windows Smart App Control blocked some compiled dependencies (pyarrow's parquet module,
  parts of scikit-learn). The project avoids them instead of disabling OS security:
  CSV storage, numpy implementations of ROC AUC and isotonic calibration, and the Claude
  and Telegram APIs called with the standard library.
- On the development network, IPv6 routes stalled HTTPS connections for 20–40 s.
  `src/net.py` forces IPv4, which cut connection times to 2–7 s.
- Some demo servers report zero spread; the executor floors the typical spread at 10 points
  so the spread filter stays active. Demo P/L is therefore optimistic versus a real account.

## Disclaimer

For education and research only. Trading leveraged FX carries a high risk of loss.
No strategy in this repository has demonstrated an edge after costs.