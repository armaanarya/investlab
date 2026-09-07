# Critique of Codex_Investment_System_Spec-2 (my independent read)

## Verdict
The spec's epistemics are excellent. Its scoping is wrong for this situation.
It is a ~6-month professional quant platform handed to a student with a game
that starts in 2 days. Keep the honesty rules, cut ~60% of the build.

## Flaws

### F1. Timeline vs scope (fatal if unaddressed)
DECA runs Sept 8 - Dec 4. Wharton trades Sept 28 - Dec 4. The spec has 5 phases;
phases 3-5 (spike ML, walk-forward, forward paper observation) cannot complete
before the games END. Phase 5 "forward paper observation" is literally the
competition itself. Fix: collapse to 2 phases that finish inside 2 weeks.

### F2. The load-bearing fact is buried
Both platforms are closed simulators with no API and manual order entry. The
spec mentions "manual competition trade entry" once in section 2 and then
specifies a 7-page UI, a Backtrader broker adapter, and an execution engine as
if fills were programmatic. The real deliverable is: a daily list of orders to
type by hand, plus a ledger that mirrors the official statement. That is a much
smaller program.

### F3. Historical DECA backtest is unbuildable, but phases assume it
Section 11.2 admits it ("label a full historical DECA portfolio backtest
unavailable") then sections 12 and 16 proceed as though it will exist. No free
source of historical individual-bond prices or clean mutual fund NAV history.
Decide once, up front: the equity sleeve gets backtested, the compliance sleeve
gets accounted-for-only. Say it in one place instead of hedging in five.

### F4. Point-in-time apparatus is priced for data we cannot buy
Sections 6.4 and 7 specify CRSP-grade survivorship-free universe handling,
filing-vintage fundamentals, and historical sector vintages. With free data none
of that is obtainable, so every claim gets downgraded to "fixed-universe
exploratory" anyway. Building the full apparatus buys nothing. Build the cheap
90%: no lookahead in features, no forward-fill, fixed ticker list declared up
front, survivorship bias disclosed loudly and permanently.

### F5. One risk profile for two opposite games
DECA = 12-week percentage-growth race, top ~25 per region advance.
Wharton = judged on strategy quality and articulation vs a client mandate.
These reward opposite behavior. The spec's defaults (0.5% risk/trade, 15
positions, 10% cash floor) are calibrated for neither. For DECA that is
closet-indexing and mathematically cannot produce a top-1% finish. See D3.

### F6. Spike prediction (section 9) is the biggest cost / lowest payoff
Rare-event classification on daily bars, few years of free data, evaluated with
the spec's own honesty rules, will almost certainly return "no robust edge."
The spec says that is a valid outcome - which is true and also means it is a
research project, not a competition tool. Cut from v1; keep as an optional
track if time remains.

### F7. Backtrader is the wrong dependency
Spec pins Backtrader as the authoritative execution layer, then spends most of
section 11 describing how its defaults are wrong for DECA (close-only fills, no
intraday stops, game-specific commissions, frozen quantities) and that a custom
broker adapter is needed. At that point the adapter IS the engine. For daily
bars, long-only, <=15 positions, a purpose-built ~400-line event loop is smaller,
more correct, and fully testable. Drop Backtrader and VectorBT.

### F8. The journal is treated as an export, not a primary artifact
Wharton judges articulation. You need contemporaneous notes across 10 weeks and
cannot reconstruct them in December. The journal must exist on day 1 and be the
easiest thing in the system to write to. Spec puts it in Phase 4.

### F9. Single-data-source fragility
yfinance is the assumed default. It breaks on Yahoo's schedule. Need a second
source and a cache that keeps working when the network does not.

### F10. No handling of the two portfolios diverging
Two accounts, two rule sets, two objectives => they should hold DIFFERENT
things. The spec's profile system allows this but never states it, and the
shared-sleeve/ownership rules in 8.4 read as if one portfolio is being managed.

## Decisions I am making

D1. Two phases, not five. Phase A (days 1-4): data, ledger, rules engine,
    sizing, daily order sheet, journal. Phase B (days 5-10): backtest of the
    equity strategy, baseline comparison, tear sheet, Wharton report exports.
D2. No API integration. Manual entry both games. Software output = order sheet.
    Software input = official statement CSV/paste for reconciliation.
D3. Two risk profiles, deliberately different. DECA aggressive (concentrated,
    tournament-optimized for rank not expected value). Wharton mandate-matched
    and conservative-by-default until the client case lands Sept 15.
D4. Own execution engine, no Backtrader/VectorBT.
D5. Equity backtest only. Compliance sleeve is accounted, not backtested.
    Disclosed once, prominently.
D6. Spike ML deferred out of v1.
D7. Journal is a Phase A CLI command.
D8. Two data sources with an on-disk cache. Cache is the source of truth for
    reproducibility.
D9. Python 3.12 via uv. Machine currently has only 3.9.6.
