# Agent guide: DECA SMG

How any coding agent (Codex, Claude, or otherwise) runs this repo day to day for
the **DECA Stock Market Game**. The code says *what* the tool does; this file is
the working agreement for *how* it is used. If a prompt and this file disagree,
follow this file and say so.

Read in this order before doing anything:

1. `ledger/deca/LEDGER.md`: positions, cash, entry stops, rule status
2. `AGENTS.md` (this file)
3. `docs/STRATEGY.md`: how the sheet decides sells, buys and sizes, and what the
   backtests do and do not show
4. `configs/deca_rulings.json` and `docs/rules/deca-verified.md`: the rules and
   the team's rulings
5. `ledger/README.md`, `research/README.md`, `README.md`, `docs/OPEN-TASKS.md`

## Scope

DECA SMG only: Sept 8 to Dec 4 2026, $100,000 start, $5 per trade, every order
filled at the 4:00 p.m. ET close. Wharton code exists in the repo but is out of
scope; do not produce Wharton sheets.

Armaan, a high school student, runs the team. SMG has no API: every order is
typed in by hand. The agent's job is a well-researched order sheet each morning
and an honest ledger, never to trade.

## Environment

- `investlab` is this repo's CLI (`src/investlab/cli.py`, entry point in
  `pyproject.toml`). Install with `uv sync`, run with `uv run investlab
  <command>`, list commands with `uv run investlab --help`. If `uv` is missing,
  `pip install uv`. Python 3.12 is installed by uv. On Armaan's Mac use
  `/opt/homebrew/bin/uv`; system python there is 3.9.
- `uv run pytest -q` must pass before any push that touches code.
- `data_cache/` is gitignored. A fresh checkout has no prices: run
  `uv run investlab data pull` first (`--days 1100` before a backtest).
- Price providers, in order: yfinance, then Alpaca, then Tiingo. Alpaca joins
  only when `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` are set (read-only data
  keys; `ALPACA_DATA_FEED=iex` if the account cannot read SIP history). Tiingo
  joins with `TIINGO_API_KEY`. Neither is set in the repo.
- yfinance sometimes fails for every symbol at once ("possibly delisted" for all
  of them, exit code 3). That is an outage, not delistings. Retry once; with a
  cache, continue and label the data stale; with no cache, say no sheet can be
  produced and fall back to Alpaca quotes for the held positions.
- Network access needed: Yahoo Finance, `data.alpaca.markets`, `finviz.com`.
- The tool reasons in America/New_York time. Schedules are Pacific.

## Tracked state

| Path | Contents | Changes |
|---|---|---|
| `ledger/deca/` | The book: portfolio, trades, cash events, splits, equity curve, statements, journal, `LEDGER.md` | Only in "After trades" |
| `research/deca/earnings.csv` | Earnings dates | Every morning, from Finviz and yfinance |
| `research/deca/sheets/` | Each morning's sheet, `.md` and `.json` | Every morning |
| `configs/deca_rulings.json` | Team rulings (bitcoin ETFs) | Only on Armaan's word |
| `runs/` | Backtest output | Gitignored |

## Commands

| Command | Use |
|---|---|
| `investlab doctor` | Cache freshness, providers, rulings, earnings calendar, deadlines |
| `investlab data pull [--days N]` | Refresh prices |
| `investlab data metadata` | Check listing venue and size against vendor data |
| `investlab rules` | Every DECA rule and whether it is met |
| `investlab daily [--save] [--json]` | **The order sheet**: sells, compliance buys, buys, held positions, alerts |
| `investlab earnings pull` / `set` / `list` | Earnings calendar |
| `investlab stop show` / `stop set -s T -p X` | Entry, trailing and active stops |
| `investlab fill -s T -a buy\|sell -q N -p PRICE --when D [--stop X] [--asset-class bond]` | Record a trade Armaan made |
| `investlab cash-adjust --amount X --kind interest\|dividend\|fee --when D` | Cash moved without a trade |
| `investlab split -s T --ratio R --when D` | Restate a held position after a split |
| `investlab reconcile --cash C --equity E --position T:SHARES:COST_BASIS ... --as-of D --save` | Prove the ledger matches SMG; exits 5 on a mismatch |
| `investlab snapshot --session D` | Record a session's closing equity; refuses non-sessions |
| `investlab ledger --refresh` | Regenerate `LEDGER.md` |
| `investlab backtest --start D --end D --out runs/...` | Replay the sheet against baselines over one window |
| `investlab backtest-rolling --start D [--end D]` | Replay it over many 62-session windows; the evidence standard for strategy changes |
| `investlab journal add` / `risk-review` | **Armaan only.** His words, never the agent's |

## Morning routine (weekdays, 6:30 a.m. Pacific)

DECA prices orders at 1:00 p.m. Pacific, mid-school-day. The sheet must arrive
before school; never schedule it later than 7:00 a.m. Pacific.

**0. Is the market open?**

```bash
uv run python -c "from datetime import datetime; from zoneinfo import ZoneInfo; import investlab.calendar as cal; d=datetime.now(ZoneInfo('America/New_York')).date(); print(d, cal.is_session(d))"
```

`False`: send one line (market closed, the holiday, the next session) and stop.
Remaining holiday: **Thu Nov 26 2026**. **Fri Nov 27 2026 closes at 1:00 p.m.
ET, so the order cutoff is 10:00 a.m. Pacific**; the sheet leads with it.

**1. Run investlab.**

```bash
git pull
uv run investlab data pull
uv run investlab doctor
uv run investlab earnings pull
COLUMNS=200 uv run investlab daily --save
```

**2. Research** every stock the sheet sells or buys and every held stock:

- **Finviz** (`https://finviz.com/stock?t=TICKER`): read *Earnings* (date and
  BMO/AMC). If it differs from the sheet, record it with
  `uv run investlab earnings set -s T -d YYYY-MM-DD -t bmo|amc --source finviz`.
  Also note sector, Recom, Target Price, Insider Trans, Short Float and the news
  headlines, as numbers and headlines only.
- **Alpaca** (read-only): the latest SIP quote against the sheet's reference
  close; any split, dividend or symbol change; news headlines from the last two
  sessions.
- If any earnings date changed, re-run `daily --save`: a report inside the
  window blocks a buy.

**3. Report**, phone-length, in this order:

1. Time-critical alerts: any SELL, an early close, stale data, a rule deadline
   (the $10,000 mutual fund and bond minimums are due **Fri Oct 23 2026, 4:00
   p.m. ET**; student names **Oct 16 2026, 4:00 p.m. ET**, mention within seven
   days).
2. **Sell**: ticker, shares, which rule fired, close against the stop.
3. **Compliance buys**: the fund order and the bond instruction.
4. **Buy**: ticker, shares, estimated cost, stop, binding constraint, score,
   and research flags. If Alpaca shows the price has moved enough that the
   share count costs more than the estimate, say what that many shares cost now
   and how many shares the estimate buys. Do not change the sheet's count.
5. **Held positions**: close, return, active stop and distance, next earnings.
6. Data freshness per source.
7. If nothing needs doing, say so in one line.
8. Reminder: after trading, send the account statistics and the trades actually
   made.

**4. Commit** `research/deca/` (sheet and earnings) and push to `main`. Never
commit `ledger/` in the morning.

## After trades: updating the ledger

**Trigger: Armaan sends BOTH his updated SMG account statistics (screenshot or
numbers: Total Equity, Cash Balance, the Equity Positions table) AND the list of
trades he actually made.** Nothing else is a trigger: not the sheet, not the
statistics alone. He may have made only some of the sheet's trades or different
ones; that is normal.

1. **Match them.** Each listed trade must show in the statistics, and every
   change in the statistics must be a listed trade (or interest, a dividend, a
   fee, a split). If they disagree, stop and ask.
2. **Record each trade** with `investlab fill`, `--when` set to the session it
   filled:
   - New position: fill price = (cost basis − 5) ÷ shares. For an addition to a
     position, use the price from SMG's transaction history.
   - The entry stop is read from that session's saved sheet. A trade the sheet
     did not propose gets the strategy's entry stop, price − 3×ATR(14). Pass
     `--stop` only if Armaan gives one.
   - Sells: `-a sell` with the sale price.
   - Bonds: `--asset-class bond` with the platform's price and quantity.
   - Interest, dividends, fees: `investlab cash-adjust`. Splits: `investlab
     split`, then `data pull`.
3. **Verify**: `investlab reconcile --cash ... --equity ... --position
   T:SHARES:COST_BASIS ... --as-of D --save` must exit 0. On a mismatch, find
   the cause. Never force it.
4. **Curve**: `investlab snapshot --session D` for every session since the last
   row of `ledger/deca/equity.csv` whose close is cached.
5. `investlab ledger --refresh`, commit `ledger/deca/` with the trades in the
   message, push to `main`. If you can only open a PR, say so: tomorrow's sheet
   reads the ledger from `main`.
6. Remind Armaan to record his reasoning with `investlab journal add`.

## Research sources and how they feed the sheet

1. **investlab** is authoritative for the universe, eligibility, DECA rules,
   sizing, sells and the ledger. Never hand-edit its share counts or add a
   ticker it did not propose.
2. **Alpaca** (read-only connector): quotes, bars, corporate actions, calendar,
   news. Never call an order, position or account-modifying endpoint, paper or
   live. Use SIP (consolidated) data: DECA fills at the consolidated close.
   Alpaca does not carry mutual funds.
3. **Finviz**: earnings dates first, then sector, analyst, insider and
   short-interest figures and headlines. Quotes are delayed; never size from
   them. Look up only the names on the sheet and in the book; do not scrape in
   bulk or build a scraper. Its futures, forex and crypto pages are irrelevant.

What research changes, and how:

- **Earnings dates** go into `research/deca/earnings.csv` and change the sheet
  on re-run. This is the one research input the tool acts on directly.
- **Everything else is a flag** in the report: a price that moved away from the
  estimate, a pending split, a news headline, heavy sector concentration.
  Flags inform Armaan; they do not reorder the sheet.
- **A flag that keeps recurring belongs in code.** Add the check to investlab
  with tests and document it in `docs/STRATEGY.md`, rather than repeating it by
  hand.

## How the sheet decides (summary; details in `docs/STRATEGY.md`)

- **Sells** fire on a completed close at or below the active stop (the higher of
  the entry stop and a ratcheting trailing stop), or when a held name's score
  falls below the exit threshold while it closes under EMA50 after a minimum
  hold. Mutual funds and bonds held for compliance are never sold by these
  rules.
- **Compliance buys** come before any discretionary buy: an S&P 500 index fund
  sized to 2% over $10,000 net cost, and instructions for an SMG bond.
- **Buys** go down the momentum ranking, each passing: score and trend filters,
  DECA eligibility, earnings, re-entry cooldown, one position per exposure group
  (all spot bitcoin ETFs are one), sector cap, aggregate open-risk cap, position
  count, minimum order size, and the drawdown policy.
- Sale proceeds are not spent the same day.

## Bitcoin ETFs

The team's ruling (`configs/deca_rulings.json`, 2026-09-12) is that spot bitcoin
ETFs are allowed, so the screen can propose IBIT like any ETF. The published
DECA text still lists bitcoin as banned, and no written confirmation exists, so
every sheet shows the ruling as CONFLICTING. Keep it that way until Armaan gives
a written source (SIFMA's answer via the advisor, or the Local Rules page) to
paste into `written_source`; if that source says no, set `value` to false.

Do not add a crypto view to the sheet: no bitcoin thesis, no weighting for news
such as the CLARITY Act. If Armaan wants a bitcoin position for his own reasons,
he says so and the tool sizes it like any other order.

## Hard rules

- **Never place trades** or log into SMG. Never place orders on Alpaca, paper
  or live.
- **Never record a trade** without Armaan's account statistics and his list of
  trades.
- **Never suggest** GLD, SLV or other commodity trusts, direct crypto, options,
  futures or currencies.
- **A bond ETF does not satisfy DECA's bond requirement**: DECA classifies every
  ETF as a stock. Only SMG-listed bonds rated BBB or better count.
- **Never write Armaan's reasoning**: no thesis, trading note, journal entry or
  drawdown review. Facts and numbers only.
- Never run tests or scripts that write into `ledger/` or `research/`. Tests
  use temp roots automatically (`tests/conftest.py`).

## Changing the strategy

- Pull history (`data pull --days 1100`) and evaluate on many rolling
  12-week windows against SPY and the compliance-aware baseline, not one long
  run: results are highly path-dependent (`docs/STRATEGY.md`).
- Change defaults in `src/investlab/config.py` and `docs/STRATEGY.md` together,
  with the evidence, in one commit.
- Every result is survivorship-biased, holds bonds as cash, and ignores
  earnings dates. Say so wherever a number is quoted.

## Date reminders

| Date | Reminder |
|---|---|
| Fri Oct 16 2026, 4:00 p.m. ET | DECA student names, hard cutoff. Mention within seven days. |
| Fri Oct 23 2026, 4:00 p.m. ET | $10,000 net cost each in stocks, mutual funds, bonds |
| Thu Nov 26 2026 | Market closed |
| Fri Nov 27 2026 | Closes 1:00 p.m. ET: order cutoff 10:00 a.m. Pacific |
| Fri Dec 4 2026, 4:00 p.m. ET | Game ends |
