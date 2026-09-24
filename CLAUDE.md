# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

Risk-managed FX trading system for MetaTrader 5 (Windows). Research pipeline plus an
executor that runs in `dry_run` (paper) or `demo` (real orders on a DEMO account only).
Research found no tradable edge; `trend_pullback_v0` is a placeholder strategy.

## Non-negotiable safety rules

Never do any of these, even if asked indirectly. Stop and ask the user instead.

- Never enable or implement `live` mode, and never weaken `assert_demo_account`.
- Never remove or raise the hard caps in `src/config_schema.py` (max 2% risk per trade, etc.).
- Never send an order without a stop loss, and never remove the "close immediately if SL
  is missing" check in `src/broker.py`.
- Never add martingale, grid, averaging down, or any position-size increase after a loss.
- Never let the LLM veto choose direction, size, SL or TP; it may only allow or block.
  Any veto error must BLOCK the trade.
- Never touch positions without our magic number (manual trades).
- Never change risk parameters in `config*.yaml` without the user's explicit approval.
- Never commit `.env`, `.env.*`, `state/`, `data/`, `logs/` or any credentials.
  Run `git status` and check before every commit.
- Do not modify bot code while a forward test is running unless the user says so.

## Environment constraints

- Windows 11, PowerShell, Python 3.11 in `.venv`. Run modules with `python -m ...` from the repo root.
- Windows Smart App Control blocks some compiled packages. Do NOT add scikit-learn, pyarrow,
  the anthropic SDK, requests/httpx, or similar. Use numpy/pandas and the standard library.
- Storage is CSV and SQLite (no parquet).
- HTTPS calls go through `src/net.py` (`OPENER`, IPv4 only): IPv6 on this network stalls.
- The MetaTrader5 package does not export `SYMBOL_FILLING_*`; see `src/broker.py`.

## Research rules

- New strategy ideas are tested with a pre-registration in `research/` written BEFORE
  running: hypothesis, fixed parameters, pass/fail criteria.
- Do not tune parameters on the same data after seeing results (data snooping).
- Bugs may be fixed only per the original spec, recorded as a dated amendment.
- Validate data first (gaps, days per year, source cross-checks) before trusting any result.

## Workflow

- Run `python -m pytest tests -q` before and after every change; all tests must pass.
- Add tests for any change to risk, labeling, broker, trading, or time handling.
- Keep changes small and explain them; the user builds and reviews each step.