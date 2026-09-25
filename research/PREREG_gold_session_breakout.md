# Pre-registration: XAUUSD H1 London-session breakout

Written BEFORE writing the test code or viewing any results. Do not edit after the test runs.

## Hypothesis
Gold's first decisive H1 close outside the Asian-session range during the London morning
predicts continuation in that direction, strongly enough to beat costs and to beat simply
being long gold at the same time of day (gold rose strongly in 2023-2026).

## Data
- Broker MT5 (Valetax Cent), XAUUSD.vxc H1, 2023-06 to 2026-09 (same data as eval_symbol)
- Old feed (observe only): 2023-06-09 .. 2024-12-31 (median spread 0, unreliable microstructure)
- New feed (decision): 2025-01-01 .. 2026-09-22 (same feed as live trading)

## Rules (fixed, no tuning)
- All session times in London local time (Europe/London, so DST is handled)
- Asian range: highest high and lowest low of H1 bars starting 00:00-06:59 London time
- Signal window: H1 bars starting 07:00-11:59 London time
- Long: first bar in the window that CLOSES above the Asian high
- Short: first bar in the window that CLOSES below the Asian low
- At most one trade per day (the first signal only)
- Entry at the next bar's open; SL = 1.0 x ATR(14) H1; TP = 2.0 x ATR(14) H1
- Time exit after 10 bars if neither barrier is hit
- Cost: spread 0.29 (live median) on every trade, plus conservative fills (SL first if both hit)

## Baseline
"Always long": a long trade with the same SL/TP/time exit, entered at the same bar on every
signal day. This separates skill from gold's uptrend.

## Pass criteria (all must hold, new feed only)
1. At least 60 trades
2. Mean R per trade > +0.10 after costs
3. Mean R positive in both 2025 and 2026 separately
4. Mean R beats the always-long baseline by at least +0.10

## If it fails
Record the result. Do not change session times, SL/TP, window or filters to make it pass.