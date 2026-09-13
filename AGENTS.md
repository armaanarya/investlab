# Agent guide

How any coding agent (Codex, Claude, or otherwise) operates this repo day to
day. The *what* lives in the code and docs; this file is the *how we work*
agreement that is not written down anywhere else.

Read in this order before doing anything:

1. `ledger/deca/LEDGER.md` — current positions, cash, rule status
2. `ledger/README.md` — how the ledger works and the four things never to get wrong
3. `README.md` — what the tool is and is not
4. `docs/OPEN-TASKS.md` — key dates, what is built, what is not
5. `docs/rules/deca-verified.md`, `docs/rules/wharton-verified.md` — sourced rules

## Who and what

Armaan, a high school student, is competing in two games at once in fall 2026:

- **DECA Stock Market Game**: Sept 8 to Dec 4 2026. $100,000 start, $5/trade,
  fills at the 4:00 p.m. ET close.
- **Wharton WInS**: trading Sept 28 to Dec 4 2026. Unverified until the
  2026-27 materials release Sept 15 2026.

Neither platform has an API. Every order is typed in by hand. The agent's job
is to produce the order sheet and keep the book honest, never to trade.

## Environment

- `uv sync`, then `uv run investlab ...`. Python 3.12 via uv. On Armaan's Mac
  use `/opt/homebrew/bin/uv`; system python there is 3.9.
- `data_cache/` is gitignored. A fresh checkout has no prices, so run
  `uv run investlab data pull` before anything else. It needs outbound access
  to Yahoo Finance (yfinance). Tiingo is the fallback only if `TIINGO_API_KEY`
  is set, and as of 2026-09-12 it is not.
- yfinance sometimes fails for **every** symbol at once, printing "possibly
  delisted" for all 74 and exiting with code 3. That is a provider outage, not
  delistings (it happened the morning of 2026-09-11 and cleared by evening).
  Say so plainly. With an existing cache, continue and label the data stale.
  With no cache, say no sheet can be produced today.
- The tool reasons in America/New_York time internally. Schedules are Pacific.

## The agreed daily loop

1. **Morning**: the agent runs the routine below and hands over the sheet.
2. **School day**: Armaan enters orders on the platform by hand before
   1:00 p.m. Pacific (4:00 p.m. ET).
3. **After the close**: Armaan sends the platform's confirmation numbers or a
   screenshot of Equity Positions. **The agent records the fills itself** with
   `investlab fill`, then commits and pushes `ledger/`. He does not want to run
   the commands.
4. **Reasoning is always his.** The agent gives him the `journal add` command
   and never pre-fills the text.

## Morning routine (weekdays, 6:30 a.m. Pacific)

DECA prices every order at the 4:00 p.m. ET close, which is 1:00 p.m. Pacific,
mid-school-day. The sheet has to arrive before school. Do not move this later
than about 7:00 a.m. Pacific.

**Step zero: is the market open?**

```bash
uv run python -c "from datetime import datetime; from zoneinfo import ZoneInfo; import investlab.calendar as cal; d=datetime.now(ZoneInfo('America/New_York')).date(); print(d, cal.is_session(d))"
```

If `False`, stop. Report one line: market closed, the holiday if known, and
the next session date. No data pull, no sheet, no suggestions.

- Remaining holiday this season: **Thu Nov 26 2026** (Thanksgiving).
- **Half day Fri Nov 27 2026**: NYSE closes 1:00 p.m. ET, so the DECA cutoff is
  **10:00 a.m. Pacific**. Lead the report with that.

**On a trading day:**

```bash
git pull
uv run investlab data pull
uv run investlab doctor
uv run investlab rules --profile deca
uv run investlab rules --profile wharton
COLUMNS=200 uv run investlab daily --profile deca
```

`COLUMNS=200` stops the order table truncating dollar amounts.

**Report**, short enough to read on a phone, time-critical items first:

- Every unsatisfied rule with the dollar amount and the due date. The DECA
  diversification requirement ($10,000 net cost in each of stocks, mutual
  funds and bonds by **Fri Oct 23 2026, 4:00 p.m. ET**) can disqualify the
  team. Surface it with days remaining whenever unmet.
- Each proposed order: ticker, action, integer shares, estimated cost, and the
  binding constraint.
- Whether the data is fresh or stale.
- If nothing needs doing, say so in one line. Do not manufacture activity.
- Remind him to send confirmation numbers after the close so the fills get
  recorded. Until they are, `daily` keeps proposing buys he already made.

Armaan often asks for the plan directly in conversation. The scheduled run is
a backstop, so keep it brief.

## Recording fills

- DECA's Equity Positions **Cost Basis includes the $5 commission**. Fill price
  = (cost basis − 5) ÷ quantity. Verified on the first three fills (2026-09-11):
  ledger cash then matched the platform's Cash Balance to the cent. Check that
  match after every batch and report any difference.
- **Always pass `--when YYYY-MM-DD`** with the session the order filled. The
  default is today in ET, which is wrong when recording after midnight ET or on
  a weekend.
- Never record the sheet's estimates as fills. Only confirmation numbers.
- After recording: `uv run investlab ledger --profile deca --refresh`, then
  commit `ledger/` and push to `main`. The morning run reads the ledger from
  `main`, so a fill left on a branch or an unmerged PR means tomorrow's sheet
  re-proposes the same buys. If you cannot push to `main`, say so explicitly.
- `investlab snapshot` stamps the row with today's date unconditionally. Only
  run it on a trading day, after the close, once `data pull` has that day's bar.
  Never on a weekend or holiday.

Open question, not yet resolved: the platform's cost basis includes commission
but the tool's diversification test (per README) uses net cost without it.
Budgeting **$10,005 gross per asset class** satisfies either reading, so keep
doing that until DECA's Local Rules page settles it.

## Alpaca market data (where an Alpaca connector is available)

Approved as an extra data source to make the order sheet better informed:
daily and intraday bars, latest quotes, corporate actions (splits, dividends),
market calendar, and news headlines as facts.

- **Read-only, always.** Never call an Alpaca order, position, or account-
  modifying endpoint, on a paper or a live account. Alpaca is not either
  competition's platform, and an order there is either meaningless or real
  money.
- **Use SIP (consolidated) bars for daily closes**, not IEX-only. DECA fills at
  the official consolidated close; an IEX-only close can differ.
- Alpaca likely does not carry mutual funds, and SMG's bond list is SMG's own,
  so keep yfinance for the fund and bond legs.
- Alpaca also lists crypto. The prohibited list below still applies in full.
- Wiring Alpaca into `investlab` itself (a provider next to yfinance and
  Tiingo) is welcome: keys from environment variables only, never committed;
  tests with no network; and the morning run keeps using the existing
  `data pull` until the new provider is merged to `main`.
- News and data are facts for Armaan to weigh. Do not turn them into a thesis
  or a reason to trade.

## Hard rules

- **Never place trades or log into either platform.** No automation of order
  entry (see "Deliberately not building" in `docs/OPEN-TASKS.md`).
- **Never suggest IBIT, GLD, SLV, any crypto, options, or futures.** Both
  competitions prohibit them. A fill going through is not permission;
  prohibited trades can be invalidated retroactively and repeat violations
  disqualify the team.
- **A bond ETF does not satisfy DECA's bond requirement.** DECA classifies
  every ETF as a stock. The bond leg must come from SMG's own bond list,
  investment grade at BBB or better. Never suggest AGG, BND or TLT for it.
- **Never write investment reasoning, a thesis, a trading note, or Investment
  Policy Statement prose.** Wharton: "You may not submit any work generated by
  an AI program as your own," and it audits trading notes. Supply facts and
  numbers; Armaan writes the reasoning via `uv run investlab journal add`.
- Never run tests or scripts that write into `ledger/`. Tests must set
  `INVESTLAB_LEDGER_ROOT` to a temp directory (see `store.py`).

## Date-sensitive reminders

| Date | Reminder |
|---|---|
| On/after Sep 15 2026 | If Wharton still reports unverified: the 2026-27 materials are out; fill in starting capital, Approved ETF List, position limits, client mandate, trading-activity deadline (`docs/OPEN-TASKS.md`). Capital was $500,000 last season; $100,000 online is wrong. |
| Oct 9 2026 | Wharton roster due 5:00 p.m. ET; provisional minimum-trading-activity deadline |
| Oct 16 2026 | DECA student names, hard cutoff 4:00 p.m. ET. Mention within 7 days. |
| Oct 23 2026 | DECA diversification deadline, 4:00 p.m. ET |
| Nov 6 2026 | Wharton Investment Policy Statement due. Mention within 7 days. |
| Nov 26 / 27 2026 | Market closed / half day (10:00 a.m. Pacific DECA cutoff) |
| Dec 4 2026 | Both competitions end |
