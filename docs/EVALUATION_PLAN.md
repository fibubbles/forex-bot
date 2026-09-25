# Evaluation plan: XAUUSD H1 micro live (Cent account)

Written BEFORE the first live gold trade. Do not change the criteria after seeing results.

## Setup
- Strategy `trend_pullback_v0`, symbol XAUUSD.vxc, timeframe H1, fixed lot 0.01
- Equity floor as set in `config.live.yaml`; news veto ON
- Historical check (eval_symbol, 2023-2026): no edge; long results follow gold's uptrend,
  short results inconsistent by year. Expected outcome: roughly break-even with noise.

## When to review
- After 30 CLOSED bot trades (not after a fixed number of weeks), or earlier if the floor is hit.
- Command: `python -m scripts.report --days 60 --mode live`

## "Worth continuing" (all must hold)
1. Net P/L after spread is positive AND larger than the estimated API cost
   (cent account: P/L in USC / 100 = USD)
2. No single trade makes up more than 50% of total profit
3. No bot errors other than occasional network timeouts

## Moving to M15 (only if all of the above hold)
1. Run `eval_symbol` on XAUUSD M15 first; signal mean R must be clearly above random in 2025-2026
2. Check that veto API cost at M15 frequency stays small relative to the account
3. Only then change the timeframe

## If the criteria fail
Record the result. Either stop, or keep running purely for learning.
No deposit top-ups to "win back" losses.