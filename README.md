# investlab

Decision support for the **DECA Stock Market Game**, Sept 8 to Dec 4, 2026.

Each trading morning it prints an order sheet: what to sell, the compliance buys
DECA requires, what to buy, and where every held position stands against its
stop. It keeps a ledger that mirrors the SMG account, enforces every DECA rule it
can verify, and replays the whole process against baselines.

The runbook for operating it day to day, by a person or an agent, is
**[AGENTS.md](AGENTS.md)**. How the sheet decides, and what the backtests show,
is **[docs/STRATEGY.md](docs/STRATEGY.md)**.

**Wharton WInS** (2026-27 rules, published Sept 15) has its own commands:
`investlab wharton plan` turns the team's chosen strategy into WInS orders,
`investlab wharton project` simulates Laura Gao's portfolio to 2033 (operating
reserve, chance all ten payments are funded, facility-contribution range), and
`investlab wharton cashflows` prints the case study's numbers. Rules:
`docs/rules/wharton-verified.md`. Strategy inputs: `configs/wharton_strategy.json`.

## What this is not

**Not connected to SMG.** SMG has no API. Every order is typed in by hand.
Nothing here authenticates to, scrapes, or posts to the platform, and nothing
places an order on any other service either.

**Not a source of your investment thesis.** DECA requires that portfolios reflect
the team's own research, and Wharton audits AI-written trading notes. The tool
emits facts: prices, ranks, costs, stops, risk numbers, rule results. You write
the reasoning; the journal stores it verbatim.

**Not a predictor.** The sheet is a disciplined process for sizing, stopping and
staying compliant. Over 30 rolling 12-week windows it beat SPY about half the
time, and a plain momentum baseline did better. See `docs/STRATEGY.md` before
trusting any number.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv.

```bash
git clone https://github.com/armaanarya/investlab.git
cd investlab
uv sync
uv run investlab --help
```

## The daily process

**Morning, before school** (orders fill at the 4:00 p.m. ET close, 1:00 p.m.
Pacific):

```bash
uv run investlab data pull
uv run investlab earnings pull
uv run investlab daily --save
```

Then check each stock on the sheet and in the book on Finviz and Alpaca (see
below) and enter the orders you choose on SMG.

**After trading**, from the platform's own numbers and the trades you actually
made:

```bash
uv run investlab fill -s CVX -a buy -q 140 -p 214.06 --when 2026-09-11
uv run investlab cash-adjust --amount 14.20 --kind interest --when 2026-09-12
uv run investlab reconcile --cash 21680.13 --equity 99985.00 --position CVX:140:29973.40 --as-of 2026-09-11 --save
uv run investlab snapshot --session 2026-09-11
uv run investlab journal add -s CVX -a buy -q 140 -p 214.06
```

`fill` is the only way a trade enters the book. Until it runs, `daily` still
believes the old positions and cash. DECA's Equity Positions cost basis includes
the $5 commission, so fill price = (cost basis − 5) ÷ shares.

## Commands

| Command | What it does |
|---|---|
| `daily [--save] [--json]` | The order sheet: sells, compliance buys, buys, held positions, alerts |
| `rules` | Every DECA rule and whether it is met |
| `doctor` | Cache freshness, providers, rulings, earnings calendar, deadlines |
| `data pull` / `data status` / `data metadata` | Prices, and a check of listing venues and sizes |
| `earnings pull` / `set` / `list` | The earnings calendar that blocks buys and flags holdings |
| `stop show` / `stop set` | Entry, trailing and active stops |
| `fill` | Record a trade that executed |
| `cash-adjust` | Record interest, a dividend, or a fee |
| `split` | Restate a position after a stock split |
| `reconcile` | Prove the ledger matches SMG; exits 5 on a mismatch |
| `snapshot --session D` | Record a session's closing equity |
| `ledger [--refresh]` | Show the book; regenerate `ledger/deca/LEDGER.md` |
| `risk-review` | Your own note that lifts a drawdown halt |
| `journal add` / `list` / `export` | Your own reasoning, hash-chained |
| `backtest --start D --end D` | Replay the sheet against five baselines and write a tear sheet |
| `backtest-rolling --start D` | Replay it over many 12-week windows and score how often it beat SPY |
| `wharton plan` | WInS orders for any sleeve outside its band, checked against Wharton rules |
| `wharton project [--strategy S] [--save]` | Monte Carlo of each strategy to 2033: reserve, funding odds, contribution range |
| `wharton cashflows` | Laura's cash flows and what the operating reserve costs at each rate |

## How the sheet decides

In short (details and evidence in `docs/STRATEGY.md`):

- **Sells** when a completed close is at or below the active stop (the higher of
  the entry stop and a trailing stop that only rises), or when a held name's
  momentum score has collapsed while it trades under its 50-day average.
- **Compliance buys** next: an S&P 500 index fund sized 2% over DECA's $10,000
  net-cost minimum, and exact instructions for an SMG-listed bond.
- **Buys** last, down the momentum ranking, each passing DECA eligibility,
  trend, earnings, cooldown, exposure-group, sector, open-risk, position-count,
  minimum-size and drawdown checks, and sized so a stop-out costs at most 2% of
  equity.

## Research sources

The sheet is one of three inputs to each morning's plan:

- **investlab** decides eligibility, share counts, sells and the ledger.
- **Alpaca** (read-only): consolidated quotes and daily bars, corporate actions,
  the market calendar, news. `data pull` falls back to Alpaca when
  `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` are set. Never used to place an
  order, paper or live.
- **[Finviz](https://finviz.com)**: the next earnings date and time first, then
  sector, analyst, insider and short-interest figures and headlines, from each
  ticker's quote page. Its quotes are delayed, so it is never a price source.

Earnings dates go into `research/deca/earnings.csv` and change the sheet: a
report inside the window blocks a buy. Everything else surfaces as a flag in the
report. See [AGENTS.md](AGENTS.md) for the routine.

## Things that will bite you

**Bitcoin ETFs rest on a team ruling, not the published rules.** The team has
ruled that spot bitcoin ETFs such as IBIT are allowed, recorded in
`configs/deca_rulings.json`, so the sheet can propose IBIT and treats every spot
bitcoin ETF as one position. DECA's published guidelines still name "futures,
options, commodities, currencies and bitcoin" as banned, and prohibited trades
can be invalidated after the fact. Every sheet shows the conflict until a written
confirmation is recorded. GLD, SLV and other commodity trusts stay blocked.

**DECA's diversification test is on net cost, not market value.** At least
$10,000 in each of stocks, mutual funds and bonds by **Oct 23, 2026, 4:00 p.m.
ET**, held to Dec 4. The $5 commission sits outside net cost, so buy above
$10,000. A position *declining* below $10,000 needs no action; *selling* out of
a class starts a one-business-day clock to restore it.

**A bond ETF does not satisfy the bond leg.** DECA counts every ETF as a stock.
Only bonds SMG lists count, rated BBB or better, in $1,000 face (Treasuries
$100). At par, $10,200 of net cost needs $11,000 face.

**Every order fills at the close.** An order entered 9:30 a.m. to 4:00 p.m. ET
fills at that day's close; after hours, at the next session's close. Limit orders
do not rest. **Fri Nov 27, 2026 closes at 1:00 p.m. ET**, so that day's cutoff is
10:00 a.m. Pacific.

**Backtests flatter.** The universe is fixed as of 2026-09-06 and
survivorship-biased, bonds are held as cash, earnings dates are not applied, and
results swing widely with the start date. Returns are never annualised.

## Repository layout

```
AGENTS.md            the operating runbook
configs/             team rulings (deca_rulings.json), Wharton strategy inputs
docs/
  STRATEGY.md        how the sheet decides, and the backtest evidence
  OPEN-TASKS.md      what is left, what is human-only, what is deferred
  rules/             verified competition rules, with sources
  codex/             prompts for handing the process to Codex
ledger/deca/         the book: portfolio, trades, cash events, curve, LEDGER.md
research/deca/       earnings calendar and saved order sheets
src/investlab/
  cli.py             every command
  plan.py            the order sheet (pure function; the backtest calls it too)
  sheets.py          saved sheets, markdown rendering, stop lookup
  screen.py          indicators and the cross-sectional ranking
  earnings.py        earnings calendar and gap sessions
  contracts.py       shared types
  config.py          rules constants and strategy parameters
  competitions/      deca.py and wharton.py rules engines
  wharton_client.py  Laura Gao cash flows and the projection engine
  cli_wharton.py     the `wharton` commands
  portfolio/         ledger, sizing, risk and drawdown, exits
  data/              cache, providers (yfinance, Alpaca, Tiingo), universe
  backtest/          event loop and baselines
  reports/           tear sheet
tests/               pytest suite; synthetic market fixtures in conftest.py
```

## Sources

Competition rules were verified against primary sources on 2026-09-06 and are
recorded with citations in `docs/rules/`. Platform facts (cost basis includes
commission, end-of-day execution) were verified on 2026-09-11. Re-check the SMG
in-portfolio Local Rules page once you can.
