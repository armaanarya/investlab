# File ownership during parallel Phase 1+2

Three agents work concurrently. Ownership is disjoint by construction so no
two agents ever edit the same file. `contracts.py` is FROZEN: read it, import
from it, do not modify it.

| Owner | Files |
|---|---|
| **CORE** | `money.py` `calendar.py` `config.py` `journal.py` `cli.py` `data/**` `features/**` `portfolio/**` and their tests |
| **DECA** | `competitions/deca.py` `tests/competitions/test_deca.py` |
| **WHARTON** | `competitions/wharton.py` `tests/competitions/test_wharton.py` |
| **controller** | `contracts.py` `backtest/**` `reports/**`, integration wiring |

The two competition modules depend ONLY on `contracts.py`. They must not
import each other, and must not import from `portfolio/` or `data/` — that is
what keeps them buildable before CORE finishes.

Ruling (controller, 2026-09-06): subagent-driven-development says never run
implementation subagents in parallel because they conflict. Overridden here
because ownership is disjoint at the file level and the competition modules
have no compile-time dependency on core internals. Cost if wrong: an
integration pass to reconcile drift, paid by the controller at wiring time.
