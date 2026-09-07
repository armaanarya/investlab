# investlab

Decision support for two high school investing competitions running at the
same time in fall 2026:

- **DECA Stock Market Game** — Sept 8 to Dec 4, 2026
- **Wharton Global High School Investment Competition** — trading Sept 28 to Dec 4, 2026

The tool prints a daily order sheet you type into each platform by hand, tracks
the resulting portfolio, enforces every competition rule it can verify, and
exports evidence for the graded deliverables.

## What this is not

**Not connected to either platform.** Neither competition has an API. Every
order is entered manually through the web UI. Nothing here authenticates to,
scrapes, or posts to either site.

**Not a source of your investment thesis.** Wharton's rules state that you may
not submit AI-generated work as your own, require a Trading Note per trade, and
audit them — the published instruction reads "You must use actual trading notes
from trades you made on WInS. (Yes, we will verify this.)" DECA requires that
portfolios reflect the team's own research.

So this tool emits facts: prices, rankings, costs, risk numbers, constraint
results, reconciliation tables. You write the reasoning. The journal prompts
you for it and stores your words verbatim. It will not draft a thesis, an
Investment Policy Statement, or a trading note, and it is built so it cannot.

**Not a predictor.** There is no claim that any strategy here beats a
benchmark. Baselines are mandatory in every backtest precisely so a failure to
beat them is visible rather than hidden.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv.

```bash
git clone https://github.com/armaanarya/investlab.git
cd investlab
uv sync
uv run investlab --help
```

## Daily use

```bash
uv run investlab data pull            # refresh the local price cache
uv run investlab daily --profile deca # today's order sheet
uv run investlab journal add          # record why you made each trade
```

Signals compute from the **previous completed close**, so run it in the
morning. That matters because of a timing difference between the two games:

- **DECA fills end of day.** An order entered any time between 9:30 a.m. and
  4:00 p.m. ET fills at *that same day's* closing price, not the price when you
  clicked. After-hours orders fill at the next business day's close. Limit
  orders do not rest — one pricing attempt, then gone.
- **Wharton fills in real time** during market hours. Displayed prices lag 10
  to 15 minutes; fills do not. After-hours orders fill at the next day's open.

A morning run gives you the whole school day to enter DECA orders before the
4:00 p.m. deadline.

## Repository layout

```
src/investlab/
  contracts.py       frozen cross-module types; the integration surface
  config.py          per-competition capital, costs, risk posture
  calendar.py        NYSE sessions and business-day arithmetic
  money.py           Decimal money; shares always round DOWN
  data/              price cache, providers, the fixed universe
  features/          indicators and cross-sectional ranking
  portfolio/         ledger, integer share sizing, risk state
  competitions/      deca.py and wharton.py, which never import each other
  backtest/          purpose-built daily event loop plus baselines
  reports/           tear sheets and chart exports
  journal.py         student-authored decision log
docs/
  rules/             verified competition rules, with sources
  superpowers/specs/ the design spec and the decisions behind it
```

## Things that will bite you

**IBIT, GLD and SLV are prohibited at both competitions.** DECA's rules name
"futures, options, commodities, currencies and bitcoin"; IBIT fails as bitcoin
exposure, as a commodity-backed grantor trust, and because the tradeable
universe is "stocks and mutual funds" and a commodity trust is neither. Wharton
bans crypto outright and separately restricts ETFs to an approved list. There
is no published exclusion list, so the interface may accept the order anyway. A
fill is not permission — prohibited trades can be invalidated after the fact
and repeat violations disqualify the team. The tool hard-blocks these.

**DECA's diversification test is on net cost, not market value.** At least
$10,000 in each of stocks, mutual funds and bonds by **Oct 23, 2026, 4:00 p.m.
ET**, held to Dec 4. The $5 commission does not count toward the minimum, so a
purchase of exactly $10,000 leaves you $5 short. Budget above it. A position
*declining* below $10,000 needs no action; *selling* starts a one-business-day
clock.

**Wharton's 2026-27 rules do not exist yet.** They release **Sept 15, 2026**.
Until a season config is supplied, the Wharton profile stays unverified and
refuses to emit an order sheet. Starting capital was $500,000 last season; the
$100,000 figure that circulates online, including in StockTrak's own boilerplate
FAQ, is wrong for this competition.

**Backtests are fixed-universe and survivorship-biased.** A survivorship-free
universe is not obtainable at zero budget: free sources give you historical
index membership including delisted names, but not their prices. The universe
is declared up front and every report says so. Individual corporate bond price
history is likewise unobtainable, so the bond sleeve is accounted for, never
backtested.

## Sources

Competition rules were verified against primary sources on 2026-09-06 and are
recorded with citations in `docs/rules/`. Re-check the Wharton pages on Sept 15
and the SMG in-portfolio Local Rules page once you have account access.
