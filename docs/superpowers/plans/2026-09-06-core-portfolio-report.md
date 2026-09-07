# Core Portfolio Accounting — Report

**Status:** Complete. All 10 tasks in `2026-09-06-core-portfolio.md` implemented via TDD (test written, run to confirm failure, implemented, run to confirm pass, committed) in the order the plan specifies. Full suite green, no changes outside the owned scope remain in the working tree.

**Commit range:** `14beaef` (`feat(money): ...`) through `2d261d0` (`feat(journal): ...`), 9 commits, interleaved with concurrent DECA/Wharton/core-data agent commits in the shared working tree:

```
14beaef feat(money): Decimal money helpers with floor-only share rounding
440f6a8 feat(ledger): cash, lots, FIFO sells, and the AccountState snapshot
e8be47f feat(ledger): value-preserving splits and once-only dividends
63a3eed feat(ledger): read-only reconciliation against the platform statement
8044584 test(ledger): property tests for the spec section 7 invariants
4ee903f feat(sizing): integer share solver with binding-constraint reporting
61aa77d test(sizing): properties for cap satisfaction, maximality, and cash reservation
bea5e23 feat(risk): open risk, concentration, cash floor, two-stage drawdown policy
2d261d0 feat(journal): append-only hash-chained log of student-authored reasoning
```

**Test summary:** `uv run pytest tests/ -v` → 278 passed, 2 skipped (unrelated live-network data-provider tests), 0 failures, 0 warnings. This layer's own tests: 9 (money) + 18 (ledger) + 4 (ledger properties) + 11 (sizing) + 4 (sizing properties) + 13 (risk) + 9 (journal) = 68 tests, all passing, including both exact worked-example variants from spec §8 ($800 planned loss / 200 shares / ceiling-bound; $806 planned loss / 199 shares / cash-bound with $5+$5 commissions) and the six hypothesis property suites (equity identity, integer/never-up share counts, split value preservation, dividend exactly-once, sizing cap satisfaction + maximality, sequential batch cash reservation).

## Concerns

**1. Contract observations to report, not fix (per the plan's self-review):**
- `AccountState` is `frozen=True` but carries a mutable `dict` field (`marks`), so the "frozen snapshot" is mutable through that dict and the dataclass-generated `__hash__` will raise if anyone tries to hash an instance.
- `BlockReason` has no member for an invalid/missing protective reference — `sizing.py` uses `INSUFFICIENT_EVIDENCE` — nor for a risk-policy halt — `risk.py`'s `DrawdownMonitor.gate_new_entry` uses `RULE_CONFLICT`. Both are reasonable stand-ins but are named-member gaps in the frozen enum.
- `SizingConstraints` has one `commission_per_trade` rather than separate entry/exit fields; both `entry_commission` and the flat part of `exit_commission` in `sizing.py` are derived from the same value.

**2. AI-authorship review (journal.py):** No part of this module generates, suggests, autocompletes, or templates student prose. `Journal.record`/`amend` require `reasoning` as a non-defaulted positional argument, store it byte-for-byte (not stripped, not normalized), and raise `EmptyReasoningError` before writing anything if it's blank. `export_evidence_packet` emits only headings, a facts table, the reasoning quoted verbatim under a heading naming the student as author, and a provenance footer (tool name, version, data sources, timestamp, APA citation line) — no narrative sentence about the trade itself. A guardrail test (`test_the_journal_never_generates_reasoning`) asserts no `generate_`/`suggest_`/`draft_`/`autocomplete`/`template_` function exists in the module and that `reasoning` has no default. Nothing in this task required generating prose a student would submit; nothing was flagged.

**3. Process incident — an early `git commit -am` swept in a concurrent agent's changes.** While committing the ledger reconciliation task, I used `git commit -am` (stage-all), which picked up unstaged edits the DECA agent had made to `src/investlab/competitions/deca.py` and `tests/competitions/test_deca.py` in the same shared working tree at that moment. Those changes landed inside my `feat(ledger): read-only reconciliation...` commit (`63a3eed`) instead of a DECA-authored commit. I did not intentionally edit either file and verified `tests/competitions/` still passes (35/35) after the fact, so the content itself is coherent and not corrupted — this is a commit-attribution/hygiene issue, not a functional break. I did not rewrite history (risky in a shared tree with other active agents) and switched every subsequent commit to `git add <exact owned files>` + `git commit -m` (no `-a`), verified via `git status --short` before each commit for the rest of the session. Flagging for the controller in case commit provenance matters for that hunk of DECA history.

**4. No blockers, no scope violations.** Only `investlab.contracts` and the standard library are imported anywhere in `money.py`, `journal.py`, `portfolio/ledger.py`, `portfolio/sizing.py`, `portfolio/risk.py`. `git status --short` at completion shows no modifications to files outside this plan's ownership (the only untracked file present, `src/investlab/cli.py`, belongs to the controller and was not touched).
