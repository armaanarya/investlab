# How the DECA order sheet decides

This is the algorithm behind `investlab daily`, every parameter it uses, and
what the backtests do and do not show. The code is `src/investlab/plan.py`
(the sheet), `screen.py` (signals), `portfolio/exits.py` (sells),
`portfolio/sizing.py` (share counts) and `competitions/deca.py` (rules).
Parameters live in `src/investlab/config.py`. Change the code, the config and
this file together.

The objective: DECA ranks teams on percent return against S&P 500 growth over a
62-session game, and only the top 25 per region advance. So the number that
matters is how often, and by how much, the book beats SPY over 12 weeks, while
never breaking a rule that disqualifies. The sheet is decision support: Armaan
decides every trade, and writes every reason.

## 1. Universe

- About 59 S&P-100-style large caps, fixed on 2026-09-06, with sector labels.
- IBIT, eligible under the team's bitcoin-ETF ruling (section 8).
- GLD and SLV, kept so the rules engine visibly blocks them.
- Five bond ETFs and four mutual funds. Neither is part of the momentum sleeve:
  bond ETFs count as stocks at DECA and cannot satisfy the bond leg, and the
  funds are for the compliance leg only.

The fixed universe is survivorship-biased: companies that fell out of the index
before 2026 are missing, which flatters every backtest.

## 2. Signals

Computed from bars through the **previous completed close**, so the morning
sheet uses yesterday's close and every order fills at today's close.

- **Score**: the mean of three cross-sectional percentile ranks across the
  sleeve (stocks and non-fixed-income ETFs): 21-session return, relative volume
  (today's volume over the prior 20 sessions' mean), and the MACD histogram
  divided by ATR(14). Reported 0-100. It is a rank, not a probability. A name
  needs 252 sessions of history, and the ranking needs at least 20 names.
- **Trend**: close against the 50-session EMA.
- **ATR(14)**: Wilder average true range on raw prices; it sets every stop.

## 3. Order of work

1. **Sells** of held positions.
2. **Compliance buys**, because missing DECA's diversification minimum
   disqualifies.
3. **New buys**, from whatever cash is left.

Sale proceeds are not spent the same day. DECA prices a sell and a buy at the
same close and does not publish which it processes first, so a buy funded by a
same-day sale could land on margin at 7% a year.

## 4. Sells

Evaluated on a completed close; the sell fills at the next close, so an
overnight gap is a real risk no stop prevents.

| Rule | Fires when | Parameter |
|---|---|---|
| Entry stop | close ≤ the stop recorded on the lot | signal close − **3 × ATR(14)** |
| Trailing stop | close ≤ highest close since entry − 5 × ATR(14), ratcheted so it never falls | **5 × ATR(14)** |
| Momentum lost | score < 40 **and** close < EMA50, after at least 5 sessions held | **40**, **5 sessions** |

The active stop is the higher of the entry and trailing stops. The sheet names
which fired, so a stop-out at a loss reads differently from giving back a gain.

- Mutual fund and bond positions held for compliance are never sold by these
  rules.
- After a sale, the same name, and every name in its exposure group, cannot be
  bought again for **10 sessions**.
- If sales would take the stocks class under $10,000 net cost, the sheet alerts:
  DECA requires restoring it within one business day.

The three lots bought on 2026-09-11 (ORCL, CVX, WFC) carry the stops from that
day's sheet, which used the earlier 2×ATR policy. They are tighter than today's
3×ATR entry stops. Whether to loosen them is Armaan's call
(`investlab stop set`).

## 5. Compliance buys

- **Mutual funds**: the first of FXAIX, VFIAX, VTSAX with a cached price, for
  at least $10,200 of net cost (2% over the minimum, so a NAV dip between the
  signal and the fill cannot leave the class short): shares = max(⌈10,200 ÷
  close⌉, 10). An S&P 500 index fund, because the forced $10,000 then tracks the
  benchmark DECA ranks against instead of adding a bet. Alternatives are listed
  in case SMG does not carry FXAIX.
- **Bonds**: the tool has no SMG bond prices, so it gives instructions: an
  SMG-listed bond rated BBB or better, $1,000 face for corporates and
  municipals ($100 for Treasuries), face = $10,200 ÷ (price ÷ 100) rounded up to
  the increment, and the highest price the remaining cash covers. The set-aside
  is the face at par plus commission ($11,005). A bond ETF does not count.
- Deadline alerts run until each class holds $10,000: **Oct 23, 2026, 4:00
  p.m. ET**.

## 6. Buys

Down the ranking, highest score first. A candidate must pass, in order:

1. Score ≥ **60** (the loop stops at the first name below it).
2. Close above EMA50.
3. Not held and not being sold today.
4. No position already held in its exposure group (all spot bitcoin ETFs are
   one group).
5. Outside the re-entry cooldown.
6. DECA eligibility: NYSE, NASDAQ or NYSE Arca; $3 or more at the close on the
   signal day and the day before; market cap of $25M or more; not a prohibited
   trust (bitcoin ETFs subject to the ruling).
7. **Earnings**: no report whose gap session falls after the signal close and
   within **5 sessions** after the fill. A stock with no date on file is flagged
   to check Finviz, not blocked.

Then it is sized, with the largest whole share count that keeps every cap:

- **Risk**: loss to the entry stop, including both commissions and the sell
  fee, ≤ **2% of equity** (halved in the reduced drawdown stage).
- **Cash**: cash minus the compliance set-asides, minus a **2% cash floor**, and
  minus buys already on the sheet.
- **Position cap**: **15% of equity**, the strategy's own limit under DECA's
  30% ceiling.
- **Sector cap**: **50% of equity** per sector; a buy that would breach it is
  shrunk to fit.
- **Minimum order**: **$2,000**, so commission stays under 0.25%.
- **Aggregate open risk**: the sum over the book of shares × (close − active
  stop) stays ≤ **12% of equity**; a position without a stop is stressed at 20%.
- **Positions**: at most **8** stocks and ETFs, and at most 8 buys a day.
- DECA's 10-share minimum on buys.

Each buy shows its planned risk and what the same position loses on a 20%
overnight gap, which no stop prevents.

## 7. Drawdown policy

Equity is tracked against its high-water mark from `ledger/deca/equity.csv`.

- **5% down**: risk per new trade is halved.
- **10% down**: no new buys. Sells still run. The halt lifts only when Armaan
  records his own review (`investlab risk-review`), which re-bases the high-water
  mark to that day's equity; a further 5% or 10% fall triggers again.

## 8. Bitcoin ETFs

The team ruled on 2026-09-12 that spot bitcoin ETFs are allowed, recorded in
`configs/deca_rulings.json`. The published DECA guidelines still ban "bitcoin",
so every sheet shows the ruling as CONFLICTING until a written source is
recorded. Under the ruling, IBIT is ranked and sized like any ETF, subject to
every other check, and never alongside another spot bitcoin ETF. The sheet
carries no view on crypto: no thesis, no extra weight for news such as the
CLARITY Act.

## 9. Parameters

| Parameter | Value | Where |
|---|---:|---|
| Entry stop | 3 × ATR(14) | `DecaStrategyConfig.entry_atr_multiple` |
| Trailing stop | 5 × ATR(14) | `trail_atr_multiple` |
| Position cap (strategy) | 15% | `max_position_weight` |
| Position ceiling (DECA rule) | 30% | `DecaConfig.position_ceiling_fraction` |
| Entry score minimum | 60 | `entry_min_score` |
| Trend filter | close > EMA50 | `entry_requires_above_ema50` |
| Momentum-lost exit | score < 40, after 5 sessions | `exit_score_below`, `exit_decay_min_hold_sessions` |
| Re-entry cooldown | 10 sessions | `reentry_cooldown_sessions` |
| Minimum order | $2,000 | `min_order_notional` |
| Earnings block / warn | 5 / 2 sessions | `earnings_block_sessions`, `earnings_warn_sessions` |
| Sector cap | 50% | `max_sector_weight` |
| New buys per day | 8 | `max_candidates` |
| Compliance buffer | 2% | `compliance_buffer_fraction` |
| Compliance funds | FXAIX, VFIAX, VTSAX | `compliance_mutual_funds` |
| Risk per trade | 2% | `RiskConfig.risk_fraction_per_trade` |
| Max positions | 8 | `max_positions` |
| Cash floor | 2% | `cash_floor_fraction` |
| Aggregate open risk | 12% | `aggregate_open_risk_fraction` |
| Drawdown reduce / halt | 5% / 10% | `drawdown_derisk`, `drawdown_halt` |

## 10. What the backtests show

**Method.** The replay (`investlab backtest`, `investlab backtest-rolling`)
calls the same `build_sheet` the morning run calls: the signal from each prior
close, fills at the next close, $5 per trade plus the sell fee. It cannot see
three things, and every number below inherits all three: bonds (the bond
set-aside sits in cash), earnings dates (the earnings filter is off), and
removed companies (the universe is survivorship-biased). In rolling runs a
drawdown halt resumes after 10 sessions, standing in for the review a live halt
requires.

**The measure that matches the game**: 30 windows of 62 sessions, one starting
every 15 sessions from 2024-09-16 to 2026-06-11 (prices from yfinance, pulled
2026-09-12). Excess is percentage points over SPY buy-and-hold in the same
window.

| Run | Mean return | Mean excess | Median excess | Worst | Best | Beat SPY | Beat SPY by 5+ | Beat compliance-aware | Avg max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Sheet, current defaults** | +6.87% | +2.58 | +0.22 | −10.45 | +32.81 | 53% | 33% | 53% | 6.79% |
| Sheet, previous defaults (2×/3× ATR, 30% cap) | +4.94% | +0.66 | −0.26 | −10.84 | +35.15 | 50% | 30% | 50% | 8.05% |
| Compliance-aware ($10,200 fund, bond cash, rest SPY) | +3.83% | −0.46 | −0.44 | −1.85 | +0.93 | 20% | 0% | — | 5.73% |
| Momentum top-8, monthly, no stops, no compliance legs | +7.72% | +3.43 | +4.23 | −7.76 | +15.55 | 67% | 50% | 67% | 8.12% |

Reproduce the current-defaults, compliance-aware and momentum rows with
`uv run investlab backtest-rolling --start 2024-09-16 --end 2026-09-11`. The
previous-defaults row and the variants below came from the same windows with the
parameters changed.

Other settings tried on the same 30 windows (mean excess, beat SPY, beat by 5+,
average drawdown): 2×/3× ATR with a 15% cap +2.38, 53%, 27%, 6.83%; 3×/5× with
3% risk +2.65, 53%, 33%, 6.97%; 3×/5× with a 12.5% cap and no momentum-lost exit
+1.18, 53%, 27%, 6.90%; 2×/4× with a 20% cap +2.17, 50%, 23%, 7.79%.

**Why the defaults changed on 2026-09-12.** The previous 2×ATR entry stop sat
inside ordinary daily noise: in the 2025-26 replay 42 of 47 closed trades ended
on a stop. With 2% risk to a 2×ATR stop, positions reached DECA's 30% ceiling,
so the book held three or four names. The current defaults beat the previous
ones on mean and median excess, beat rate, beat-by-5 rate and drawdown, with a
similar worst case.

**Why no single backtest is evidence.** On the same one-year window
(2025-09-02 to 2026-09-11), the previous defaults returned +16.8%, then +42.6%
after an $800 change to the bond set-aside; moving the start date by one week at
a time gave anywhere from −6 to +34 points against SPY. The current defaults
return +9.7% on that exact window against SPY's +19.3%, yet do better across all
30 windows. A concentrated momentum book is path-dependent: one early trade
changes every later one.

**What this means for the game.** In this sample the sheet beats SPY over 12
weeks about half the time, by a small average margin, with smaller drawdowns
than SPY. It is not a demonstrated edge. The plain momentum baseline did better,
though it holds none of DECA's forced $20,000 in the fund and bond legs and has
no stops. The case for the sheet is rule compliance, sizing and risk discipline,
not prediction.

## 11. Changing the strategy

1. `uv run investlab data pull --days 1100`.
2. Change the parameter in `config.py` (or the rule in code, with tests).
3. `uv run investlab backtest-rolling --start 2024-09-16` and compare against
   the table above: mean and median excess, beat rates, worst case, drawdown.
4. Adopt a change only if it improves the rolling results broadly, not one
   window or one number.
5. Update the parameter table and section 10 here, `docs/OPEN-TASKS.md`, and
   commit it all together with the evidence.
