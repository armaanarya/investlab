# DECA SMG 2026-27 HS - verified facts (research 2026-09-06)

## Execution: END OF DAY, confirmed
- Order entered 9:30a-4:00p ET on day T  -> fills at day T's CLOSING price.
  Cancellable in Pending Orders until 4:00p ET.
- Order entered after 4:00p / weekend / holiday -> fills at NEXT business day's close.
- Limit orders DO NOT REST: "will not be held past the initial attempt to price
  the transaction." One shot, then gone.
- Consequence: intraday timing is worth exactly nothing. Signal computed after
  Monday's close, entered Tuesday morning, fills Tuesday close. 1-day lag.

## Money, dates, costs
- Start $100,000. Margin up to 50% of Total Equity.
- Sep 8 2026 9:30a ET -> Dec 4 2026 4:00p ET.
- Student names due Oct 16 2026 4:00p ET. HARD. No additions after.
- $5 commission per trade, both sides. SEC fee on sells (rate UNVERIFIED).
- Interest: 7.00%/yr charged on negative cash, 0.75%/yr credited on positive.
  Daily accrual, posted Saturdays.
- Margin maintenance ~30% (SECONDARY source only). Below it -> margin call;
  unresolved after 7 days -> SMG AUTO-LIQUIDATES your positions.
- Ranking: Percent Return vs S&P 500 Growth, "net of any borrowed funds."
  No end-of-game liquidation required. No ranking at all until first trade posts.
- Top 25 per DECA REGION -> ICDC. Only students submitted by Oct 16 can rank.

## Eligible universe
- NASDAQ and NYSE only. No OTC/pink sheets. NYSE American UNVERIFIED.
- Stocks: >= $3/share (day before AND day of), >= $25M market cap.
- Minimum 10 shares on buys and short sells. Sells/covers may be < 10.
- Whole orders only. No partial fills.
- ETFs allowed and classified as STOCKS - including BOND ETFs.
- Bond mutual funds are classified as MUTUAL FUNDS.
- Bonds: only SMG-provided, investment grade BBB or higher. Muni/corp $1,000
  increments, Treasury $100.
- SHORT SELLING IS ALLOWED (margin account). Shorts do NOT count toward
  diversification.
- BANNED: futures, options, commodities, currencies, bitcoin.
- SMG may liquidate thin/volatile names and may cancel transactions at will.

## IBIT verdict: DO NOT BUY
Three independent grounds against: (1) "bitcoin" named in the ban; (2) IBIT is a
commodity-backed grantor trust and "commodities" are named in the ban; (3) rule 3's
universe is "stocks and mutual funds" and a commodity trust is neither. No published
exclusion list exists, so the interface MIGHT let the order through - and a fill is
not permission. Prohibited trades can be invalidated retroactively; repeat violations
disqualify the team. SIFMA holds sole disqualification authority.
Same logic bans GLD, SLV and other physically-backed commodity trusts.
To resolve: advisor must email decasmg@sifma.org (students get no reply).

## Team ruling 2026-09-12: spot bitcoin ETFs allowed (NOT confirmed in writing)
Armaan (team captain) ruled that spot bitcoin ETFs such as IBIT are allowed in
DECA SMG. The tool follows the ruling, recorded in `configs/deca_rulings.json`:
- IBIT is eligible, subject to every other check (exchange, price, market cap).
- Every spot bitcoin ETF counts as ONE exposure: the sheet never holds two.
- GLD, SLV and other commodity trusts, direct crypto, futures, options and
  currencies stay prohibited.
The verdict above is still the reading of the published text, so every sheet
reports the ruling as CONFLICTING until a written source (SIFMA's answer via the
advisor, or the in-portfolio Local Rules page) is pasted into `written_source`.
If that source says bitcoin ETFs are banned, set `value` to false.
Listing venues checked 2026-09-12: IBIT is Nasdaq-listed (eligible). FBTC and
ARKB list on Cboe BZX, which the NYSE/NASDAQ rule excludes.

## Verified on the platform, 2026-09-11
- The portfolio page shows "Trade Type: ENDOFDAY" and "Ranking Method: S&P500".
- Equity Positions "Cost Basis" INCLUDES the $5 commission: 140 CVX filled at
  $214.06 shows $29,973.40. Fill price = (cost basis - 5) / shares.
- After the first three fills, Cash Balance ($21,680.13) and Total Equity
  ($99,985.00) matched the ledger to the cent.

## Diversification - HARD RULE
$10,000 minimum NET COST in EACH of stocks / mutual funds / bonds by
**Friday Oct 23 2026 4:00p ET**, held through Dec 4 2026 4:00p ET.
- Measured on NET COST at purchase, minus the $5 fee. Budget slightly over $10k.
- Value DECLINING below $10k -> no action required.
- SELLING -> must restore a $10k holding in that class within ONE BUSINESS DAY.
- Only LONG stock positions count toward the stock bucket.
- Random portfolio audits. Violations can disqualify.

## Date conflict from the original spec: DOES NOT EXIST
Oct 2 2026 = Fall COLLEGIATE session diversification deadline.
Oct 23 2026 = Fall HIGH SCHOOL session. Portal and PDF agree exactly on HS dates.

## Automation
No documented API. A private JSON API must exist (iOS/Android apps) but is
undocumented and unsupported. No published ToS clause forbids bots; none permits
them either. Rule 22: the team is responsible for ANY transaction entered in its
portfolio. P1: portfolios must reflect the team's OWN research and strategy.
Since every intraday order gets the same closing price, automation buys nothing.
=> Enter trades by hand. Automate the analysis, not the execution.

## Still unverified - check once logged in
1. In-portfolio Resources -> Rules of the Game -> LOCAL RULES. Highest value.
2. Whether IBIT/GLD even resolve in the ticker search.
3. The SMG Security Table (which mutual funds and bonds actually exist).
4. Whether the platform offers CSV export of holdings/transactions.
5. Exact SEC fee rate; exact maintenance margin %.
6. NYSE American eligibility.
