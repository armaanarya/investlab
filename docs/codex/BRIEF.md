# Codex brief: investlab for DECA SMG

Paste everything below the line into Codex to start the handoff. The recurring
morning task's prompt is in `docs/codex/MORNING_TASK.md`.

---

You're taking over the daily operation of investlab, the Python CLI I (Armaan) use for the DECA Stock Market Game this fall. Until now Claude Code ran it on my Mac. Run the exact same process in Codex. This handoff is DECA only; ignore Wharton.

Repo: https://github.com/armaanarya/investlab (private). You have GitHub access.

READ FIRST, IN THIS ORDER
1. AGENTS.md: the runbook. It is authoritative; if this prompt and AGENTS.md disagree, follow AGENTS.md and tell me.
2. ledger/deca/LEDGER.md: positions, cash, entry stops, rule status.
3. docs/STRATEGY.md: how the order sheet decides sells, buys and sizes, and what the backtests do and don't show.
4. configs/deca_rulings.json and docs/rules/deca-verified.md.
5. README.md, docs/OPEN-TASKS.md, ledger/README.md, research/README.md.

READ, USE AND UPDATE INVESTLAB
Everything runs through the investlab CLI in the repo: `uv sync`, then `uv run investlab <command>` (`pip install uv` if it's missing; `uv run investlab --help` lists the commands). Keep the repo current: when the process, the tool or the strategy changes, update the code, tests, AGENTS.md, docs/STRATEGY.md and docs/OPEN-TASKS.md in the same commit, and run `uv run pytest -q` before pushing.

YOUR DATA AND RESEARCH TOOLS (part of your reasoning on every sheet)
1. investlab: authoritative for eligibility, DECA rules, sizing, sells and the ledger. Never hand-edit its share counts or add a ticker it didn't propose.
2. @alpaca, read-only: SIP quotes and daily bars, splits and dividends, the market calendar, news. Never place, change or cancel an order or touch any Alpaca account setting, paper or live. If you can expose read-only Alpaca keys to the repo environment as APCA_API_KEY_ID and APCA_API_SECRET_KEY, `investlab data pull` falls back to Alpaca automatically when yfinance fails.
3. Finviz (https://finviz.com/stock?t=TICKER): the next earnings date and time (BMO/AMC) first, then sector, analyst, insider and short-interest figures and headlines, for every held stock and every stock the sheet sells or buys. Record earnings dates with `uv run investlab earnings set -s T -d YYYY-MM-DD -t bmo|amc --source finviz` and re-run the sheet, because a report inside the window blocks a buy. Finviz quotes are delayed; never size from them. Look up only the names on the sheet and in the book.
Your environment needs internet access to Yahoo Finance, data.alpaca.markets and finviz.com.

EVERY WEEKDAY MORNING AT 6:30 A.M. PACIFIC
Set up a recurring weekday task at 6:30 a.m. America/Los_Angeles that runs the morning routine in AGENTS.md and sends me the order sheet: what to SELL (if anything), the DECA compliance buys, what to BUY, held positions against their stops, and research flags. DECA prices orders at 1:00 p.m. Pacific, so it has to arrive before school; never later than 7:00 a.m. Use the prompt in docs/codex/MORNING_TASK.md. If you can't create the schedule yourself, tell me and I'll set it up.

AFTER I TRADE: UPDATING THE LEDGER
Only update the ledger when I send you BOTH (1) my updated SMG account statistics (a screenshot or the numbers) and (2) the trades I actually made. I may make only some of the sheet's trades, or different ones. Never record a trade from the sheet itself. Then follow "After trades" in AGENTS.md: record each fill (fill price = (cost basis − $5) ÷ shares, --when set to the fill session), record any interest, dividend or fee with `investlab cash-adjust`, confirm `investlab reconcile` passes against my statistics, backfill `investlab snapshot --session` for each session since the last row, and commit and push ledger/ to main. If my statistics and my list of trades disagree, ask me before recording anything.

BITCOIN ETFS
My team's ruling is that spot bitcoin ETFs such as IBIT are allowed in DECA SMG. It's recorded in configs/deca_rulings.json, so the screen can propose IBIT like any ETF, and it treats all spot bitcoin ETFs as one position. The published DECA text still lists bitcoin as banned and we have no written confirmation, so every sheet shows that conflict; keep showing it until I give you a written source to record. Don't add a crypto thesis or news bet (for example the CLARITY Act) to the sheet. If I want a bitcoin position for my own reasons, I'll tell you and you size it with the tool.

NEVER
- Place trades or log into DECA SMG, or place orders anywhere, including Alpaca.
- Record a trade without my account statistics and my list of trades.
- Suggest GLD, SLV or other commodity trusts, direct crypto, options, futures or currencies.
- Offer a bond ETF (AGG, BND, TLT) for DECA's bond requirement. DECA counts every ETF as a stock.
- Write my investment reasoning, theses, trading notes, journal entries or drawdown reviews. Give me facts and numbers; I write the words with `uv run investlab journal add` and `uv run investlab risk-review`.

WHERE THINGS STAND (Sept 12 2026)
- Holdings: ORCL 142 @ $150.28 (entry stop $147.68), CVX 140 @ $214.06 (stop $205.62), WFC 299 @ $90.29 (stop $85.95). Cash $21,680.13, equity $99,985.00, both matching SMG.
- Still due by Fri Oct 23 2026, 4:00 p.m. ET: $10,000 net cost in mutual funds and $10,000 in bonds. Monday's sheet proposes 39 FXAIX and an SMG-listed bond rated BBB or better.
- Earnings on file from yfinance: WFC Oct 13 (before open), CVX Oct 30 (before open), ORCL Dec 10 (time unknown). Confirm each on Finviz.

FIRST SESSION
1. `uv sync`, `uv run pytest -q`, `uv run investlab data pull`, `uv run investlab doctor`.
2. `COLUMNS=200 uv run investlab daily` and confirm it shows the three positions with their stops and cash of $21,680.13.
3. Confirm @alpaca returns a quote for ORCL and that ORCL's Finviz quote page loads.
4. Confirm you can push to main.
5. Set up the 6:30 a.m. Pacific weekday task, or tell me you can't.
Report back briefly on what worked and what didn't.
