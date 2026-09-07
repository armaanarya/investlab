# Open tasks

Last updated 2026-09-07.

What is built, what is not, and what only a human with a competition login can
do. Anything marked **BLOCKED** cannot be started until someone supplies
information that does not exist yet.

Key dates, for context on urgency:

| Date | What |
|---|---|
| Sep 15 2026 | Wharton 2026-27 materials release |
| Sep 28 2026 | Wharton trading opens |
| Oct 9 2026 | Wharton roster due, 5:00 p.m. ET |
| **Oct 16 2026** | **DECA student names due, 4:00 p.m. ET. Hard cutoff.** |
| **Oct 23 2026** | **DECA diversification deadline, 4:00 p.m. ET** |
| Nov 6 2026 | Wharton Investment Policy Statement due |
| Dec 4 2026 | Both competitions end |

---

## Built and working

- Price cache, providers (yfinance primary, Tiingo fallback), fixed universe
- NYSE calendar and business-day arithmetic
- Ledger: FIFO sells, value-preserving splits, once-only dividends, statement reconciliation
- Integer share sizing with binding-constraint reporting and gap stress
- Risk: aggregate open risk, concentration, two-stage drawdown policy
- DECA rules engine: eligibility, diversification clock, position ceiling, EOD execution semantics
- Wharton rules engine: season state machine, trade budget, commission drag
- Candidate screen and the daily order sheet
- Hash-chained journal with evidence export
- Weekday 6:30 a.m. Pacific scheduled task

---

## Not built

These are evidence for the December deliverables rather than parts of the
daily loop, which is why they were not blocking. They become the priority once
the daily loop has been running for a week or two.

### 1. Backtest engine

A purpose-built daily event loop. Deliberately not Backtrader, which is
unmaintained since 2023 and raises `AttributeError` on Python 3.12 because
`lineiterator.py` still calls `collections.Iterable`.

- Daily event loop over cached bars, carrying real cash and open positions
- Two execution profiles: DECA's same-session close, and a generic next-open
- Corporate actions applied through the existing ledger, not a second engine
- Deterministic seeds and a run manifest recording the cache hash

**Scope limit, decided and not to be relitigated:** the equity sleeve gets
backtested. The compliance sleeve is accounted for, never backtested. Free
sources give historical index membership including delisted names but not
their prices, and individual corporate bond history costs $2,000/yr through
FINRA Enhanced TRACE. Every report that touches bonds says so.

### 2. Baselines

Mandatory, and they run before any strategy result is reported. A strategy
that cannot beat these has produced a valid negative result, not a bug.

- Cash with its stated interest policy (DECA credits 0.75%/yr on positive balances)
- Buy-and-hold SPY, total return
- Equal-weight across the eligible universe
- Simple momentum ranking, no filters
- **A compliance-aware baseline for DECA** holding the required $10,000 in each
  class. Without this the comparison is dishonest, since the real portfolio
  carries a constraint the benchmark does not.

For DECA specifically, also report against **S&P 500 growth**, which is the
official ranking metric. Do not silently substitute a total-return ETF for a
price index.

### 3. Tear sheets and chart exports

- Standalone HTML plus CSV/JSON, with a provenance footer citable in APA
- Gross and net returns, with no annualising of a twelve-week game
- Max drawdown depth and duration, exposure, turnover, transaction-cost drag
- Trade and order ledgers, MAE/MFE, gap losses
- PNG chart exports sized for the DECA deck and the Wharton report
- Every output carries the fixed-universe survivorship-bias label

### 4. Statement reconciliation import

`Ledger.reconcile` exists and is tested. What is missing is the front end.

- **BLOCKED**: needs someone to check whether the SMG holdings page exports CSV
- If it does: a file importer
- If it does not: a paste parser for the Account Holdings table
- Wire to `investlab reconcile --statement <file> --profile deca`

---

## DECA Stock Market Game

### Urgent, human-only

- [ ] **Read the in-portfolio Local Rules page.** `Resources → Rules of the
      Game → Local Rules`. Both primary sources say DECA-specific rules live
      there and may carry text not in the published PDF. Highest-value
      unresolved item in the whole project.
- [ ] **Check whether SMG exports CSV** from Account Holdings and Transaction
      History. Decides how reconciliation gets built.
- [ ] **Search `IBIT` and `GLD` in the trading interface.** Not to buy them.
      To learn whether the platform's security table even lists them, which
      tells us whether the ban is enforced at entry or only after the fact.
- [ ] **Have the advisor email `decasmg@sifma.org`** for written confirmation
      on anything borderline. Students get no reply directly, and SIFMA holds
      sole authority over disqualification.
- [ ] Submit student names before **Oct 16, 4:00 p.m. ET**. Hard cutoff, no
      substitutions after, and only students submitted by then can rank top 25.

### Unverified rules to resolve

| Item | Current handling |
|---|---|
| SEC fee rate on sells | Assumed `0.0000278`, labeled an assumption in every output |
| Minimum maintenance margin % | 30% from a secondary source only; margin is disabled in v1 anyway |
| NYSE American eligibility | Rejected with an "unverified" reason rather than guessed either way |
| **NYSE Arca eligibility** | **Ruled eligible.** Review this one. Rule 3 names NYSE and NASDAQ, but the guidelines explicitly permit ETFs, and nearly every US ETF lists on Arca. If wrong, the platform rejects the order at entry, which is visible rather than silent. |
| The SMG Security Table | Unpublished. Which mutual funds and bonds actually exist is unknown until login. |

### Code to write

- [ ] Exit rules for held positions. The daily loop currently proposes entries
      and reports rule status; it does not yet propose exits.
- [ ] The one-business-day replacement clock needs a live trigger. The logic
      and tests exist; nothing yet watches for a sale and starts the timer.
- [ ] Margin as a late-season variance lever. Config gate exists
      (`margin_enable_after = 2026-11-01`), the accounting does not.
      **Only build this if the qualifying line is close in November.** 7%/yr
      interest and a maintenance breach triggers automatic liquidation after
      seven days.
- [ ] Bond and mutual fund selection helper for the compliance sleeve. Right
      now the tool reserves the $20,200 but does not suggest what to put it in.

---

## Wharton WInS

### BLOCKED until Sep 15

Everything here needs the 2026-27 materials, released via SurveyMonkey Apply
to registered teams. The profile currently refuses to emit an order sheet and
prints a research-only ranking instead, which is correct and should stay that
way until the real numbers land.

- [ ] **Fill in the season config.** Create `configs/wharton_2026_27.json`:

```json
{
  "starting_cash": "500000",
  "approved_etfs": ["...copy the Approved ETF List..."],
  "commission_per_trade": "25",
  "min_price": "5"
}
```

`load_wharton_season()` validates it and flips the profile to verified.
It rejects an empty `approved_etfs`, since Wharton requires holding at
least one approved ETF and an empty list cannot be correct.

- [ ] **Confirm starting capital.** It was $500,000 in 2025-26 and tracks the
      client's scenario. The $100,000 figure circulating online, including in
      StockTrak's own boilerplate FAQ page, is wrong for this competition.
      Do not hard-code whatever it turns out to be.
- [ ] **Copy the Approved ETF List verbatim.** Every ETF is blocked until this
      exists.
- [ ] **Record the client mandate**: goals, time horizon, risk tolerance,
      required withdrawals, restrictions. Last season's shape was $500,000
      initial, $1.5M target over ten years, $10,000/yr drawdown from year three.
- [ ] **Confirm the position ceiling.** Platform setting; the 2026-27 value is
      unpublished.
- [ ] **Confirm the minimum-trading-activity deadline.** Last season it was an
      early-October date, and missing it makes you ineligible for Semifinals.
- [ ] **Re-read the FAQ, Rules, and Trading pages on Sep 15.** Last season's
      Trading page carried a note that rules would keep being updated until
      trading began.

### Then integrate into the CLI

Once the season config exists, Wharton needs the same treatment DECA already
has. The rules engine is done; the wiring is not.

- [ ] `investlab daily --profile wharton` should emit a real order sheet, not
      a research ranking. The gate is already in place and lifts automatically
      when `check_rules` returns no INCOMPLETE checks.
- [ ] **Mandate mapping.** Every proposed position needs a field tying it to a
      stated client objective. This is the thing Wharton actually judges, and
      the tool should make an unmapped position visible.
- [ ] **Trade budget enforcement in the daily loop.** `TradeBudget` exists and
      is tested. The daily loop does not yet consume it, so the 40-trade
      self-imposed budget and the 200 hard cap are not enforced end to end.
- [ ] **A low-turnover screen.** DECA's momentum screen is wrong for Wharton.
      At $25 a trade against a 200-trade cap, with the organizers stating
      outright that this is not a trading game, Wharton needs a
      hold-for-months screen the student reviews, not a signal that fires.
- [ ] **IPS evidence export** ahead of Nov 6. Facts and figures only.
- [ ] **Final report chart exports** ahead of Dec 4.
- [ ] Wharton-specific baselines in the backtest, using its real commission
      structure rather than DECA's.

### A constraint that shapes all of the above

Wharton's rules state: "You may not submit any work generated by an AI program
as your own." Every trade requires a Trading Note, and Wharton audits them.
The published deliverable instruction reads: "You must use actual trading
notes from trades you made on WInS. (Yes, we will verify this.)"

So none of the exports above may draft a thesis, an IPS, or a trading note.
They emit facts, figures, and constraint results. The student writes the
argument, and the journal stores it verbatim. Anything AI-produced that does
end up in a submission gets cited in APA like any other source.

---

## Shared

- [ ] **Exit rules.** Neither profile proposes exits yet. This is the largest
      functional gap in the daily loop.
- [ ] **Refresh universe metadata.** Exchanges and market caps were resolved
      from vendor metadata on 2026-09-07 and are baked into
      `data/universe.py`. There is no refresh command yet; a stale market cap
      will not block anything, but a wrong exchange would.
- [ ] **Quantify the survivorship bias** rather than only disclosing it. The
      `fja05680/sp500` dataset gives point-in-time S&P membership back to 1996
      including delisted names, so the number of names missing per year is
      countable even though their prices are not obtainable. Reporting that
      count is a stronger move in a writeup than the disclaimer alone.
- [ ] **Set up Tiingo** as the fallback provider before yfinance breaks rather
      than after. Free tier: 500 unique symbols/month, 1,000 requests/day,
      30+ years of history. Set `TIINGO_API_KEY` and the chain uses it
      automatically.

---

## Deliberately not building

Recorded so these do not get re-proposed.

- **Spike-prediction ML.** Rare-event classification on a few years of free
  daily bars, evaluated honestly, will almost certainly return "no robust
  edge." That is a research project, and the games end Dec 4.
- **Point-in-time fundamentals from EDGAR.** Turning XBRL into correct
  filing-vintage quarterly data is a week of work minimum, and the `filed`
  field is a date rather than a timestamp, so you must join companyfacts to
  submissions on `accn` for the real acceptance time. The quality strategy is
  optional; this is not worth it before December.
- **A web UI or a deployed dashboard.** It would hold live positions and
  strategy on a public URL during a competition you are ranked in, could not
  place a single trade since neither platform has an API, and needs hosting
  and upkeep for twelve weeks. The CLI plus published artifacts covers it.
- **Intraday and order-book views.** Requires data we do not have, and DECA
  prices at the close regardless of when the order was entered.
- **Automated order entry.** No rule in either competition explicitly forbids
  it, and none permits it. DECA makes the team liable for any transaction
  entered in its portfolio, Wharton requires students to place trades through
  the shared account, and DECA's end-of-day pricing means automation buys
  nothing an alarm clock does not.
