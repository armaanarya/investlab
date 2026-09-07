# Core Data + Features Layer — Report

**Status:** Complete. All 7 implementation tasks in `2026-09-06-core-data.md` (calendar, universe, cache, providers, indicators, prefix invariance, ranking) built via TDD — failing test written and confirmed red, then implementation, then confirmed green — in the order the plan specifies. Full suite green offline; the two live-network tests (yfinance pull, Tiingo no-key skip) also verified green against the real network with `INVESTLAB_LIVE=1`. No changes outside the owned scope (`calendar.py`, `data/**`, `features/**`, their tests) remain in the working tree.

**Commit range:** `7279421` (`feat(calendar): ...`) through `d3d15ad` (`chore(tests): ...`), 7 commits, interleaved with concurrent DECA/Wharton/core-portfolio/controller commits in the shared working tree:

```
7279421 feat(calendar): NYSE sessions 2020-2027 with business-day arithmetic
e2d347f feat(universe): fixed bias-labeled universe with trust flags
f886dd8 feat(cache): parquet cache as the reproducibility boundary
b449a31 feat(providers): yfinance primary with retry, tiingo fallback, chain
2ae0515 feat(indicators): EMA/MACD/RSI/ATR/vol/breakout/relvol with exact warm-up
7ef9895 test(features): prefix invariance across all indicators
d3d15ad chore(tests): rename ambiguous single-letter locals, prefer min() over sorted()[0]
```

`src/investlab/features/ranking.py` and `tests/features/test_ranking.py` (Task 7) are also complete and present at HEAD, but landed inside another agent's commit — see Concern 1.

**Test summary:** `uv run pytest tests/ -v` → 296 passed, 2 skipped (live-network, correctly gated), 0 failures. This layer's own tests: 38 (calendar) + 12 (universe) + 18 (cache) + 21 (providers, +2 live) + 22 (indicators) + 5 (prefix invariance, parametrized at cut points 50/120/200/299) + 13 (ranking) = 129 offline + 2 live, all passing. `INVESTLAB_LIVE=1 uv run pytest tests/ -m live -v` → 2 passed (real yfinance pull of AAPL + BRK.B confirms the dot comes back; Tiingo confirms it no-ops without `TIINGO_API_KEY`).

## Concerns

**1. Process incident — `ranking.py`/`test_ranking.py` landed in another agent's commit.** I wrote `src/investlab/features/ranking.py` and `tests/features/test_ranking.py` to disk (TDD red-then-green, both passing) but before I ran `git add`/`git commit` for them, the controller's `329d156 feat(cli): wire the journal, with add/list/export` commit (their own timestamp, concurrent session) swept `tests/features/test_ranking.py` into a `git add -A`-style commit alongside their `cli.py` change, and the later `899ed4a chore: ruff config, project-wide format, lint pass` commit picked up `ranking.py` the same way. By the time I went to commit Task 7 myself, `git diff HEAD -- src/investlab/features/ranking.py tests/features/test_ranking.py` was empty — the content already at HEAD was byte-identical to what I'd written, so there was nothing left for me to commit. I did not edit either file after discovering this, and the full suite (including `screen.py`'s use of `percentile_ranks`/`InsufficientCoverage`, which the controller wired against my exact interface) passes. Functionally correct; a commit-attribution/hygiene issue only, not a code defect. I did not rewrite history in a tree with other active agents.

**2. Contract observation to report, not fix.** `contracts.Bar` and `PriceProvider` were sufficient as specified — no defect found in the parts this layer consumes. One pre-existing style note carried over from `contracts.py` (not something I introduced): `ruff`'s `UP042` flags `class X(str, Enum)` in favor of `enum.StrEnum` on `AssetClass`, `Action`, `BlockReason`, and `RuleStatus` in the frozen `contracts.py` itself. I kept `MissingPolicy(str, Enum)` in `ranking.py` matching that established, unfixed convention rather than deviating to `StrEnum` — flagging rather than fixing per the frozen-file rule, and for consistency I didn't fix it in my own file either.

**3. Plan's yfinance "verified fact" needed one refinement, applied in the implementation.** The plan states an unknown ticker "comes back with that ticker's columns present and zero rows" — true for a single-ticker request (confirmed live). For a *mixed* request (one valid symbol + one invalid), live testing showed the invalid symbol's columns instead come back with the *same row count* as the valid symbol, filled with NaN, not zero rows. `YFinanceProvider`'s retry trigger is therefore "zero bars survived NaN-row dropping for this symbol," not "zero raw rows" — this handles both shapes correctly and is what `test_missing_symbol_returns_no_bars_and_does_not_raise` and `test_rows_with_nan_ohlc_are_dropped` exercise. No code concern, just a precision note in case the plan document is revisited.

**4. Tiingo live path is genuinely unverified**, as the plan anticipates: no `TIINGO_API_KEY` was available in this environment. Contract tests run against a recorded fixture (`tests/data/fixtures/tiingo_aapl.json`); `available()`/no-key-skip behavior was confirmed live; the actual HTTP parsing path (`urllib.request.urlopen` + Tiingo's real JSON shape) has not been exercised against the live API.

**5. No scope violations.** Only `investlab.contracts`, the standard library, `pandas`/`numpy`/`pyarrow`/`pandas-market-calendars`/`yfinance` (all pre-installed, per plan) are imported anywhere in `calendar.py`, `data/cache.py`, `data/providers.py`, `data/universe.py`, `features/indicators.py`, `features/ranking.py`. `money.py` is never imported — indicators stay float, money stays Decimal, converted exactly once at `frame_from_bars`. `git status --porcelain` at completion shows no modification to `portfolio/`, `competitions/`, `journal.py`, `cli.py`, `money.py`, `config.py`, or `pyproject.toml`; the only untracked file present, `src/investlab/screen.py`, belongs to the controller (integration wiring) and was read but not touched.
