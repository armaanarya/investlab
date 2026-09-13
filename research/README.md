# Research files (DECA)

Inputs and outputs of the morning routine. **Not the ledger**: nothing here
records a trade, so these files are committed every morning.

| Path | What it is | Written by |
|---|---|---|
| `deca/earnings.csv` | Next earnings date per stock, with before-open (`bmo`), after-close (`amc`) or `unknown` timing and the source. Dates recorded from Finviz (`--source finviz`) are never overwritten by an automatic pull. | `investlab earnings set`, `investlab earnings pull` |
| `deca/sheets/<fill session>.md` | The order sheet as sent to Armaan: sells, compliance buys, buys, held positions, alerts. | `investlab daily --save` |
| `deca/sheets/<fill session>.json` | The same sheet, machine-readable. `investlab fill` reads the stop a buy was sized against from here. | `investlab daily --save` |

A report's price impact lands on its *gap session*: the session itself for a
before-open report, the next session for an after-close report, both when the
time is unknown. The sheet blocks a new buy whose gap session falls between the
signal close and five sessions after the fill, and flags a held position whose
gap session is within two sessions.
