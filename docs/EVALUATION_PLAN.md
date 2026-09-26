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

## Amendment 1 (2026-09-25), before the first live trade
News veto turned OFF for this experiment. Reason: at 0.01 cent lots the expected result per
trade is about $0.01 while each veto call costs about $0.06, so the veto would cost several
times more than it could protect. Risk per trade is already capped (~$0.25), and the spread
filter still blocks entries during spread spikes. API cost criterion therefore becomes ~$0.
M15 was evaluated with eval_symbol (2025-06 to 2026-09): edge over random small and
inconsistent (mostly long 2025), so the timeframe stays H1.

## Amendment 2 (2026-09-26), before the first bot trade
Manual trades were made on the bot's Cent account and RM28 was withdrawn, so equity fell
from 1488.79 to 1004.96 USC. Equity floor lowered from 750 to 500 USC (~20 losses of room).
Evaluation starts from 1004.96 USC. From now on the bot's account is not traded manually;
manual trading uses a separate account.

## Amendment 3 (2026-09-26), before the first bot trade: secondary observation
Study 5 (candle confirmation) failed only on the random-subset test. As a secondary,
observe-only check on NEW data: after 30 closed live trades, split them by whether the
signal bar matched the Study 5 candle definitions (engulfing or pin bar in the trade
direction) and compare mean R. This never changes the bot during the experiment and does
not affect the primary pass/fail decision. If confirmed trades are fewer than 10, report
the numbers and draw no conclusion.