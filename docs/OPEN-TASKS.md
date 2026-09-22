# Open tasks (DECA SMG)

Last updated 2026-09-18. DECA first; Wharton (rules published 2026-09-15) at the bottom.

| Date | What |
|---|---|
| **Fri Oct 16 2026, 4:00 p.m. ET** | **DECA student names due. Hard cutoff.** |
| **Fri Oct 23 2026, 4:00 p.m. ET** | **$10,000 net cost in each of stocks, mutual funds, bonds** |
| Thu Nov 26 2026 | Market closed |
| Fri Nov 27 2026 | Early close 1:00 p.m. ET: cutoff 10:00 a.m. Pacific |
| Fri Dec 4 2026, 4:00 p.m. ET | Game ends |

---

## Built and working

- Price cache and providers: yfinance, Alpaca (read-only SIP bars, when keys are
  set), Tiingo (when its key is set), tried in that order
- Universe metadata check against vendor data (`data metadata`)
- NYSE calendar and business-day arithmetic, early-close alert for Nov 27
- Ledger: FIFO sells, entry stops on lots, cash events (interest, dividends,
  fees), stock splits with a split-adjusted trade history, equity curve with a
  non-session guard and backfill, reconciliation against SMG account statistics
- Integer share sizing with binding-constraint reporting and gap stress
- Risk: 2% risk per trade, 15% strategy position cap under DECA's 30% ceiling,
  50% sector cap, 12% aggregate open-risk cap, 8 positions, 2% cash floor,
  two-stage drawdown policy (halve risk at 5%, halt at 10% until a recorded
  review)
- DECA rules engine: eligibility, diversification clock (now fed the real sell
  history), position ceiling, end-of-day execution, bitcoin-ETF team ruling
- **The order sheet**: sells (entry stop, ratcheting trailing stop, momentum
  lost), compliance buys (sized fund order, bond instructions with face and
  affordable price), buys (score and trend filters, earnings block, re-entry
  cooldown, one position per exposure group, minimum order size), held-position
  review, alerts, saved `.md` and `.json`
- Earnings calendar: Finviz or manual dates that pulls never overwrite, yfinance
  pull with before-open and after-close timing, gap-session logic
- Backtest: daily event loop replaying the same sheet code, five baselines,
  tear sheet (HTML, CSV, JSON, chart) with MAE/MFE and a provenance footer
- Journal, drawdown reviews, evidence export
- `AGENTS.md` runbook and Codex prompts (`docs/codex/`)

---

## Human-only (DECA)

- [ ] **Get written confirmation on spot bitcoin ETFs.** Have the advisor email
      `decasmg@sifma.org`, or copy the in-portfolio Local Rules text. Paste it
      into `written_source` in `configs/deca_rulings.json`; if it says no, set
      `value` to false. Until then every sheet shows the ruling as CONFLICTING.
- [ ] **Read the in-portfolio Local Rules page.** `Resources → Rules of the
      Game → Local Rules`. Highest-value unresolved item.
- [ ] **Submit student names before Oct 16, 4:00 p.m. ET.**
- [ ] **Place the compliance orders** (the sheet's fund order and an SMG bond,
      BBB or better) well before Oct 23.
- [ ] **Check whether FXAIX is on SMG's security list.** The sheet lists VFIAX
      and VTSAX as alternatives.
- [ ] **Check whether SMG exports CSV** from Account Holdings and Transaction
      History. Decides how bulk import gets built.
- [ ] **After each trading day, send the account statistics and the trades
      actually made**, so the ledger can be updated and reconciled.
- [ ] Sign up for a Tiingo key if a second fallback provider is wanted.

## Unverified rules

| Item | Current handling |
|---|---|
| Spot bitcoin ETFs | **Allowed by team ruling (2026-09-12), CONFLICTING** with the published ban on bitcoin. Shown on every sheet. |
| SEC fee rate on sells | Assumed `0.0000278`, labeled an assumption |
| Minimum maintenance margin % | 30% from a secondary source; margin disabled |
| NYSE American eligibility | Rejected as unverified |
| NYSE Arca eligibility | Ruled eligible (ETFs are permitted and nearly all list on Arca) |
| Minimum shares on mutual fund buys | Unknown; the sheet uses at least 10 |
| The SMG Security Table | Unpublished; which funds and bonds exist is unknown |

## Open code and research tasks (DECA)

1. **Strategy edge.** Over 30 rolling 12-week windows (2024-09 to 2026-09), the
   current defaults beat SPY in 53% of windows by +2.6 points on average; a plain
   monthly top-8 momentum baseline with no stops and no compliance legs beat it
   in 67%, by +3.4 points. Worth testing inside the sheet: a monthly rebalance
   sell rule for names that fall out of the top ranks, and a per-window
   compliance-aware comparison. Change defaults only on evidence across many
   windows (`docs/STRATEGY.md`).
2. **Bulk statement import.** `reconcile` accepts a JSON statement or inline
   numbers. A parser for SMG's CSV export or pasted Equity Positions table is
   still open. **BLOCKED** on the CSV check above.
3. **Dividend and interest capture.** `cash-adjust` records them by hand from
   the platform. Automatic detection (for example from Alpaca corporate actions)
   is not built; reconciliation catches a missed one.
4. **Split cash-in-lieu.** `split` rounds shares down and does not credit cash
   for fractions; reconcile shows the difference, recorded with `cash-adjust`.

## Deliberately deferred or not building

- **Margin.** Config gate exists (`margin_enable_after = 2026-11-01`), the
  accounting does not. Build only if the qualifying line is close in November:
  7%/yr interest, and a maintenance breach liquidates after seven days.
- **Quantifying survivorship bias** with point-in-time S&P membership. A writeup
  item, not part of the daily process.
- **Point-in-time earnings history** for the backtest. Not obtainable free, so
  the backtest ignores earnings and says so.
- **Spike-prediction ML, point-in-time fundamentals, a web dashboard, intraday
  views.** Reasons unchanged: no robust edge likely, a week of data work, public
  exposure of live positions, and DECA prices at the close regardless.
- **Automated order entry.** No rule permits it, DECA makes the team liable for
  every transaction, and end-of-day pricing means automation buys nothing.
- **Scraping Finviz.** Per-ticker lookups by the agent only; an automated feed
  would be Finviz's paid Elite export.

---

## Wharton WInS

Rules, exact deadlines, and submission requirements:
`docs/rules/wharton-verified.md` (2026-27, checked 2026-09-21).
Commands: `investlab wharton cashflows | project | plan`, `rules --profile
wharton`. Strategy inputs: `configs/wharton_strategy.json`.

| Date | What |
|---|---|
| **Mon Sep 28** | Trading opens |
| Fri Oct 9, 5 p.m. ET | Team roster (members locked) |
| **Fri Oct 23, 5 p.m. ET** | Trading Notes Analysis: 3 executed trades with notes |
| **Fri Nov 6, 5 p.m. ET** | IPS due; trading ends; portfolio frozen |
| Mon Nov 9 | Final Report instructions released |
| Fri Dec 4, 5 p.m. ET | Final Report and school documentation |

### Human-only (Wharton)

- [ ] **Pick the strategy** and make `active` in `configs/wharton_strategy.json`
      yours: change weights, the reserve rule, the flexibility buffer.
- [ ] **Replace every placeholder return and volatility** with a cited source
      (the plan and projections print a banner until you do).
- [x] **Verify the 2026-27 AI policy.** It permits brainstorming and idea
      generation, prohibits submitting AI-generated work as the team's own,
      and requires citations for any AI-generated material that is included.
- [ ] Write every Trading Note in WInS at the time of the trade, in your own
      words. Record each fill with `investlab fill --profile wharton`.
- [ ] Check which U.S. Treasuries WInS lists for 2036-2042 maturities (the
      ladder's later rungs).
- [ ] Roster by Oct 9; request school documentation early.

### Open code tasks (Wharton)

1. **Bond positions in the ledger.** WInS Treasuries price per 100 face and
   the cache cannot price them; `fill --asset-class bond` records them at cost.
   A face-value/clean-price model and a manual mark command are not built.
2. **Yield-driven ladder.** The ladder's discount rate is a typed assumption;
   pulling actual yields for 2032-2035 iBonds and pricing the reserve from them
   is not built.
3. **Evidence export for the Final Report**: charts of the projection
   distributions and the funding-probability-vs-reserve curve (numbers only).
4. **Sleeve-level performance** in `ledger/wharton/LEDGER.md`.
