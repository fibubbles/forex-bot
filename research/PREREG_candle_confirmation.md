# Pre-registration: candle confirmation on trend_pullback_v0 (XAUUSD H1)

Written BEFORE writing the test code or viewing any results. Do not edit after the test runs.

## Question
Does requiring a reversal candle (engulfing or pin bar) in the trade direction on the signal
bar improve trend_pullback_v0 signals enough to matter after costs?

## Data
- Cached file data/raw/XAUUSD.vxc_H1.csv (fetched 2026-09-25 for Study 4, 24,999 bars,
  2022-07-25 .. 2026-09-25). No refetch.
- Old feed (observe only): before 2025-01-01
- New feed (decision): 2025-01-01 onward (same feed as live trading)

## Base signals
trend_pullback_v0 exactly as in src/strategy.py on src/features.py features:
BUY = D1 trend up, close below EMA20, close above EMA50; SELL = the mirror.

## Candle definitions (on the signal bar i, using bars i-1 and i only)
body = |close - open|, range = high - low (skip if range = 0)
lower_wick = min(open, close) - low, upper_wick = high - max(open, close)
- Bullish engulfing: bar i-1 bearish, bar i bullish, close_i >= open_(i-1) and open_i <= close_(i-1)
- Bearish engulfing: mirror
- Hammer: lower_wick >= 0.6 x range and upper_wick <= 0.15 x range
- Shooting star: upper_wick >= 0.6 x range and lower_wick <= 0.15 x range
Confirmed BUY = base BUY and (bullish engulfing or hammer)
Confirmed SELL = base SELL and (bearish engulfing or shooting star)

## Outcome
Triple barrier from src/labeling.py defaults (TP 1.5 x ATR, SL 1.0 x ATR, 30-bar time exit),
entry at next bar open, spread 0.29, conservative fills. Unit = signal bar (overlapping
signals counted, same as eval_symbol).

## Pass criteria (all must hold, new feed only)
1. At least 60 confirmed signals
2. Confirmed mean R exceeds base mean R by at least +0.10
3. Confirmed mean R >= base mean R in both 2025 and 2026 separately
4. Confirmed mean R > +0.10 after costs
5. Random-subset test: in 10,000 random draws (seed 42) of the same number of base signals,
   fewer than 5% have mean R >= the confirmed mean R (p < 0.05)

## If it fails
Record the result. Do not change the candle definitions, thresholds or barriers to make it pass.

## Result (2026-09-26): FAIL
New feed: base 995 signals, mean R +0.068; confirmed 91 signals, mean R +0.181.
Criteria 1-4 passed (+0.113 over base; 2025 +0.278 vs +0.071; 2026 +0.087 vs +0.064).
Criterion 5 failed: random subsets of 91 base signals matched or beat +0.181 in 18.8% of
10,000 draws (p = 0.188), so the improvement is not distinguishable from chance.
Old feed (observe): confirmed +0.008 vs base +0.022 (worse than base).
Post-hoc observation (NOT a result): by pattern, hammer +0.282 (39), shooting star +0.562 (8),
bear engulfing +0.071 (14), bull engulfing 0.000 (30). Too small and chosen after the fact;
any pattern-specific variant needs its own pre-registration and new data.