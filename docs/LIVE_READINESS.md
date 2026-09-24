# Live readiness checklist

Every box must be ticked before any real money is deposited. Tick a box only with evidence
(report output, MT5 history, Telegram screenshot). Write the date next to each tick.

## A. Mechanics proven on the demo account

- [ ] Ran in demo mode for at least 3 weeks
- [ ] At least 10 trades opened by the bot (not by hand)
- [ ] Every bot trade had SL and TP set at the broker (check MT5 History)
- [ ] At least one stop-loss exit and one take-profit exit recorded in the DB with the same
      price and P/L as MT5 History
- [ ] Restart test: stopped the bot while a position was open, restarted it; no duplicate
      position, and the open position still appeared in /status
- [ ] /closeall tested once with an open position; position closed and bot paused
- [ ] Friday cutoff closed an open position before the weekend (if a position was open on a Friday)
- [ ] Watchdog alerted when the bot was stopped during market hours, and reported recovery
- [ ] `python -m scripts.report --days 7` shows no errors other than occasional network timeouts
- [ ] Veto blocks, if any, gave reasons about USD/EUR high-impact events only

## B. Broker checks (Valetax Cent account)

- [ ] Cent account opened (no deposit yet)
- [ ] `check_connection` on the Cent account: currency, contract size, tick value, min lot noted
- [ ] Position sizing check: with the Cent balance, 1% risk and a typical SL gives at least
      the minimum lot (otherwise the bot will never trade)
- [ ] Live mode implemented with extra locks (explicit confirmation, server allowlist,
      maximum lot cap) and reviewed with tests
- [ ] First live run uses the minimum lot only
- [ ] Broker is NOT on the SC Investor Alert List (check https://www.sc.com.my/investment-checker)
- [ ] Minimum withdrawal is below the planned deposit (test a small withdrawal first)

## C. Money rules (agreed before depositing)

- [ ] Deposit only an amount I am fully prepared to lose (RM50)
- [ ] Never top up from study or living money, and never deposit more to "win back" losses
- [ ] If the kill switch triggers: stop, review, and do not simply reset and continue
- [ ] Withdraw profits (if any) regularly instead of letting them compound unchecked

## D. Honest expectation

- [ ] I understand that the current strategy (`trend_pullback_v0`) has no demonstrated edge.
      Going live with it is paying for real-world experience, and the expected result is a
      slow loss after spread costs. Profit is not the goal of this stage.