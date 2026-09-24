# Pre-registration: multi-pair FX time-series momentum portfolio

Written BEFORE downloading or viewing data for these pairs. Do not edit after the test runs.
Known in advance: EURUSD single-pair momentum FAILED (PREREG_momentum.md). EURUSD is
1 of 10 pairs here; the decision uses the full portfolio only.

## Hypothesis
An equal-risk portfolio of FX time-series momentum positions earns positive returns
after costs, even though individual pairs are noisy.

## Data
- Dukascopy D1 bid, 2005-01-01 to 2026-09-24, trading days only (Mon-Fri rows)
- Universe (fixed): EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD, EURGBP, EURJPY, GBPJPY
- In-sample (observe only): 2005-2018 | Out-of-sample (decision): 2019-01-01 .. 2026-09-24

## Rules (fixed, no tuning)
- Weekly rebalance at Friday close; positions held the following week
- Per pair: direction = sign of past N trading-day return, N in {60, 125, 250}
  (about 3, 6, 12 months). ALL three are reported
- Per-pair weight = direction x (10% / realised annualised vol of the last 60 trading days) / 10
  (equal risk per pair)
- Cost per unit of weight change: majors 2 pips, crosses (EURGBP, EURJPY, GBPJPY) 3 pips;
  pip = 0.01 for JPY pairs, 0.0001 otherwise

## Pass criteria (all must hold, out-of-sample only)
1. At least 2 of 3 lookbacks net positive
2. Mean Sharpe across the 3 lookbacks > 0.3
3. For each positive lookback: still positive after removing its best year
4. For each positive lookback: still positive after removing its best-contributing pair

## If it fails
Record the result and close this research line. No changes to universe, N, weights or costs.
If it passes, tradability with a small cent account is a SEPARATE check before any use.

## Amendment 1 (2026-09-24), before re-run
First run was INVALID: the downloader (no retries, batch size 10) silently skipped whole
calendar years for 8 of 10 pairs (e.g. GBPJPY 2009-2010, NZDUSD 2025), so only 155 IS and
194 OOS weeks had all 10 pairs, and year-boundary price jumps were fake returns.
First-run result (FAIL, mean OOS Sharpe -0.69) is kept on record but not used.
Fix: re-download all 10 pairs with retries and smaller batches; verify zero holes and a
2005-01-03 start for every pair before re-running. No change to rules or criteria.
The re-run result is final.

## Amendment 2 (2026-09-24), before any valid run
Dukascopy rate-limited the re-download (HTTP 429, repeated), leaving 8 of 10 pairs
incomplete. Data source switched to MT5 D1 bars from the broker (Valetax), dated by
server trading day. Validation rule set BEFORE fetching: weekly-return correlation with
the complete Dukascopy EURUSD and AUDUSD files must be >= 0.99, otherwise the MT5 data
is rejected. OOS 2019-2026 must be fully covered; IS may start later if broker history
is shorter. No change to rules, universe or criteria.

## Amendment 3 (2026-09-24), before any valid run
MT5 validation passed (weekly corr vs Dukascopy: EURUSD 0.9990, AUDUSD 0.9974).
EURJPY and GBPJPY are only listed as '.vxb' variants on this broker, so they are built
synthetically from validated majors: EURJPY = EURUSD x USDJPY, GBPJPY = GBPUSD x USDJPY
(close prices, same server trading day). Cross-check rule set BEFORE building: weekly-return
correlation with the broker's '.vxb' series must be >= 0.99. Costs for these pairs stay at
3 pips. No change to rules, universe or criteria.


## Result (2026-09-24): FAIL (final, valid run)
Data: MT5 D1 (validated vs Dukascopy, corr >= 0.997), 10 pairs, no holes, 730 IS / 404 OOS weeks.
OOS 2019-2026 net of costs: N=60 Sharpe -0.60 | N=125 -0.54 | N=250 -0.13. 0/3 positive.
IS 2005-2018 was already weak (Sharpe 0.12 / 0.24 / -0.02).
Losses spread across most pairs; diversification did not rescue the strategy.
Conclusion: FX time-series momentum (weekly, equal-risk, 10 pairs) is not tradable after costs.
Research line closed. Invalid first run and amendments 1-3 are documented above.