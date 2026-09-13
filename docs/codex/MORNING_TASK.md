# Codex recurring task: DECA morning sheet

Schedule: weekdays, 6:30 a.m. America/Los_Angeles (never later than 7:00 a.m.).
Paste everything below the line as the task prompt.

---

Run the DECA morning routine in AGENTS.md from https://github.com/armaanarya/investlab (pull main first). Step zero is the market-open check; on a closed day, send me one line and stop. Otherwise run investlab (data pull, doctor, earnings pull, `COLUMNS=200 uv run investlab daily --save`), check every held stock and every stock the sheet sells or buys on Finviz (record any earnings date that differs with `investlab earnings set --source finviz`, and re-run the sheet if one changed) and with @alpaca, read-only (latest SIP quote against the sheet, splits and dividends, news headlines). Commit and push research/deca/. Then send me a phone-length report: time-critical alerts first, then what to SELL, the compliance buys, what to BUY with research flags, held positions against their stops, and data freshness. Do not update the ledger, do not place trades anywhere, and do not write investment reasoning.
