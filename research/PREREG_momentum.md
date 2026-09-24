# Pre-registration: EURUSD D1 time-series momentum

Written BEFORE looking at any results. Do not edit after the test runs.

## Hypothesis
The sign of EURUSD's past return predicts the sign of its future return
(time-series momentum), strongly enough to survive realistic costs.

## Data
- Dukascopy EURUSD D1 bid, 2005-01-01 to 2026-09-24
- In-sample (observe only): 2005-01-01 .. 2018-12-31
- Out-of-sample (decision): 2019-01-01 .. 2026-09-24

## Rules (fixed, no tuning)
- Signal at each weekly rebalance (Friday close): sign of the past N-day return
- N in {20, 60, 250} (about 1, 3, 12 months). ALL three are reported, none is "picked"
- Position: +1 long / -1 short, held until the next weekly rebalance
- Cost: 2.0 pips per position change (spread + slippage)

## Pass criteria (all must hold, out-of-sample only)
1. At least 2 of the 3 lookbacks have positive net return after costs
2. Average Sharpe across the 3 lookbacks > 0.3
3. Result not driven by a single year (removing the best year keeps it positive)

## If it fails
Record the result. Do NOT adjust N, rebalance day or cost to make it pass.


## Amendment 1 (2026-09-24), before re-run
Bug found after the first run: the Dukascopy D1 file contains a row for every
calendar day (median 365 rows/year), so N was counted in calendar days instead of
trading days as specified. Fix: drop Saturday/Sunday rows so N counts trading days.
No other change. First-run result (FAIL, mean OOS Sharpe -0.12) is kept on record.
The re-run result is final.


## Result (2026-09-24): FAIL
OOS 2019-2026, trading-day lookbacks, 2 pips per change:
- N=20: net -13.2%, Sharpe -0.24
- N=60: net -31.7%, Sharpe -0.57
- N=250: net +16.5%, Sharpe +0.29
Criteria: 1/3 positive (need 2), mean Sharpe -0.17 (need > 0.3).
Sign of results flips between IS and OOS for every lookback: no stable effect.
Conclusion: single-pair EURUSD time-series momentum is not tradable. Do not revisit
with other N or rebalance days on this data.