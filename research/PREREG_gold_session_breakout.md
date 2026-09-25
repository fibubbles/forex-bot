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


## Result (2026-09-25): FAIL
Deviation: the fetched data starts 2022-07-25 (25,000 bars) instead of 2023-06; this only
affects the observe-only old-feed block, not the decision.
New feed 2025-2026: 253 trades, win 32.0%, mean R +0.054 (need > +0.10);
2025 -0.007, 2026 +0.158 (need both > 0); beats always-long by +0.242 (pass).
Old feed 2022-2024 (observe): 438 trades, mean R -0.096.
Post-hoc observation (NOT a result): new-feed shorts +0.248 R over 109 trades. Any
short-only variant must be pre-registered separately and tested on NEW data (forward test),
never on this sample.