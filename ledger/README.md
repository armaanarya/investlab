# Ledger

The book that mirrors the DECA Stock Market Game account. The platform is the
authoritative record; this copy exists so the order sheet knows what is held,
at what cost, with what stops, and so the season has an auditable history.

```
ledger/
  deca/       DECA Stock Market Game    ($100,000, Sept 8 - Dec 4 2026)
  wharton/    Wharton WInS              (out of scope for the DECA handoff)
```

The two are separate on purpose: a combined book would let a Wharton position
appear to satisfy a DECA requirement, which is the exact mistake that
disqualifies a team.

## When the ledger changes

**Only when Armaan sends both his updated SMG account statistics and the list
of trades he actually made.** He may make only some of the sheet's trades, or
different ones, so the order sheet is never a source of fills. If the statistics
and his list disagree, ask before recording anything. The full procedure is
"After trades" in [AGENTS.md](../AGENTS.md).

## Files in `deca/`

| File | What it is | Written by | Edit by hand? |
|---|---|---|---|
| `portfolio.json` | Cash, positions, lots, and each lot's entry `stop`. The source of truth. | `fill`, `stop set`, `cash-adjust`, `split` | Only to correct an error |
| `trades.csv` | Append-only log of every recorded fill, oldest first. | `fill` | No |
| `cash_events.csv` | Interest credits, dividends and fees: cash that moved without a trade. | `cash-adjust` | No |
| `splits.csv` | Stock splits applied to held positions. | `split` | No |
| `equity.csv` | One equity and S&P benchmark row per trading session. | `snapshot` | No |
| `statements/<date>.json` | The platform statistics each reconciliation was checked against. | `reconcile --save` | No |
| `risk_reviews.jsonl` | Armaan's own notes that lift a drawdown halt. | `risk-review` | **Never** |
| `journal.jsonl` | Armaan's own reasoning per trade. Hash-chained, append-only. | `journal add` | **Never** |
| `LEDGER.md` | **The performance report.** Regenerated on every write. | every writer | **Never**, it is overwritten |

## For an agent picking this up cold

Read `LEDGER.md` first: equity and P&L split into realised and unrealised,
return against the S&P benchmark DECA ranks on, every open position with its
entry stop, every closed trade, DECA asset-class totals against the $10,000
minimums, the equity curve, cash events, and the raw trade log.

Returns there are **period returns, never annualised**. A twelve-week game does
not have an annual return.

Then know these things:

1. **Nothing here places trades.** Every order is typed into SMG by hand.
2. **Record from the platform's numbers.** DECA's Equity Positions cost basis
   includes the $5 commission, so fill price = (cost basis − 5) ÷ shares. Always
   pass `--when` with the session the order filled.
3. **Reconcile before committing.** `investlab reconcile` must exit 0 against
   the statistics Armaan sent.
4. **DECA counts every ETF as a stock**, bond ETFs included. Only bonds SMG
   itself lists count toward the bond minimum, BBB or better.
5. **Never write the journal or a drawdown review.** Those are Armaan's words.

## Commands

```bash
uv run investlab fill -s CVX -a buy -q 140 -p 214.06 --when 2026-09-11
uv run investlab stop set -s CVX -p 205.62
uv run investlab cash-adjust --amount 14.20 --kind interest --when 2026-09-12
uv run investlab split -s NVDA --ratio 10 --when 2026-06-10
uv run investlab reconcile --cash 21680.13 --equity 99985.00 --position CVX:140:29973.40 --as-of 2026-09-11 --save
uv run investlab snapshot --session 2026-09-11
uv run investlab ledger --refresh
```
