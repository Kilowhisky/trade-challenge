---
description: Weekly whole-market sweep. Regenerates research/universe.md, the working universe the daily run sweeps in full.
---

# /weekly-universe — one whole-market pass

Design: `docs/superpowers/specs/2026-08-17-weekly-universe-agent-design.md`.

A weekly-universe pass is a **read-only** whole-market sweep — Nasdaq Trader's
full symbol directory, reduced by a script, never by eyes on quote text — that
regenerates `research/universe.md`, the working universe the daily
`/deep-research` run sweeps in full. It answers one question: *what does the
whole tradeable market look like this week, before the daily tier narrows it
further?*

**This pass never reads a quote.** Payloads go to disk and
`scripts/universe-filter.sh` reduces them. If you find yourself reading quote
text, stop — that is the failure this design exists to remove.

## §Dispatch — run the sweep in the `weekly-universe` subagent

The parent does **not** execute §B–§D itself:

1. Parent checks §A. If either precondition fails, stop here — do not
   dispatch.
2. Parent dispatches **`weekly-universe`** (`.claude/agents/weekly-universe.md`)
   **in the background** — a ~75-call whole-market sweep has no reason to
   block the session, and this pass never competes with a live tick or
   research loop for the token (§A.2).
3. When the agent's result arrives, parent logs its return lines and, on a
   `FAIL:`, decides whether Chris needs a heads-up — the agent itself never
   escalates (it has no means to).

Cron registration mechanics for a weekly cadence are an open item in the
design doc (§9.3) — until resolved, run this command manually at a weekend
session open rather than assuming a standing schedule.

Fallback chain on a `FAIL:` about missing tools, same as tick.md/research.md
§Dispatch: first a `general-purpose` subagent prompted to obey
`weekly-universe.md`; then inline §B–§D as a last resort.

---

## §A — Preconditions

1. `scripts/check-consistency.sh` passes. A FAIL means a rule has drifted;
   fix before sweeping.
2. Market closed (`get_market_hours` for today's date, or the weekday/weekend
   check if the tool is unavailable). This is a weekend pass; it must never
   compete with a live session for the token.

## §B — The sweep

1. **Symbols.** `scripts/universe-fetch.sh --out /tmp/universe-syms.txt`
   - Exit 0: proceeds normally.
   - Exit 2 (usage): a defect in this command, not a data problem — stop,
     record the miss in §D.
   - Exit 3 (unreachable, or the pre-filtered result fell below the
     1000-symbol sanity floor): **the script writes no output file at all.**
     Stop, **keep the existing `research/universe.md`**, and record the miss
     in §D. Discovery degrades to last week, never to nothing.
2. **Chunk.** Split the fetched symbol list into 150-symbol chunks. 150 is
   chosen so each verbose response (~260 KB) reliably exceeds the inline
   tool-result limit and is written to a file by the harness instead of
   landing in context.
3. **Quote each chunk:** `get_quotes(symbols=<chunk>, verbose=True)`. Use
   `verbose=True`, not `fields=` — the design spike found `fields` silently
   ignored and `verbose` the only working switch (see `schwab-mcp-notes`).
   - The response is saved to a file and the path returned. **Record the
     path; do not read the file.**
   - If a chunk ever returns inline instead, write it to a temp file yourself
     in the same `{"result": "..."}` shape and carry on identically.
   - A chunk that errors is logged and skipped. One bad chunk never stops the
     sweep.
4. **Filter, once, over every payload.** The rank size is
   `working_universe_size` — under the `strategy:` section of `rules.yml`
   (currently 500) — never hard-coded; read it through `lib-rules.sh`:

   ```bash
   . scripts/lib-rules.sh && load_rules
   N="$(rule_get strategy_working_universe_size)"
   scripts/universe-filter.sh \
     --payload PATH1 --payload PATH2 ... \
     --qualified-only --rank-top "$N" \
     --out /tmp/universe-ranked.tsv
   ```

   Exit 0 success. Exit 2 usage — a defect in this command; stop, record in
   §D. Exit 7 — `rules.yml` missing, unreadable, or incomplete: **abort the
   sweep entirely** (per the design doc's failure-mode table, this is not a
   degrade-gracefully case — a caps file the pre-order gate itself can't read
   is a stop-everything condition), keep the existing `research/universe.md`,
   record in §D.

   Read stderr: a `"skipped N unquotable symbol(s)"` line when any symbol
   could not be quoted, and a `"ranked N of M (dropped D)"` line reporting
   qualified vs. ranked vs. dropped. Both belong in the §D ledger.

## §C — Write the working universe

Rewrite `research/universe.md` via
`scripts/research-replace.sh universe <<'EOF' ... EOF`. The script enforces the
required first line `# Fallback universe` and the banner text
`never a source for order parameters`, both verbatim — get either wrong and
the write is refused (exit 1), not silently corrected. Include, in the body:

- assembly timestamp and symbol counts (fetched / quoted / qualified / ranked
  / dropped)
- the ranked table: symbol, price, 10-day ADV, dollar volume, % from 52-week
  high, optionable, last earnings date, ETF flag — the nine columns
  `universe-filter.sh` emits
- a plain statement that the playbook §4 tilts (50-day SMA, 3/6-month
  returns) are **not** applied here and belong to the daily tier — the
  quote payload this pass reads has no price-history field to compute them
  from

## §D — Run ledger

Append one row via
`scripts/data-append.sh events DATE '<json>'`:

```json
{"t":"HH:MM:SS","kind":"weekly_universe","fetched":N,"chunks":N,"chunks_failed":N,
 "quoted":N,"qualified":N,"ranked":N,"dropped":N,"schwab_calls":N,"skipped":"<or ->"}
```

`DATE` is the ET calendar date from `get_datetime`, never the machine clock.
On a §B.1/§B.4 abort, still append this row — `fetched`/`quoted`/etc. as far
as the sweep got, and note the failure point in `skipped`.

## §E — Never, in this pass

- Never read a quote payload into context — pass paths to the filter.
- Never place, cancel, or modify an order. This agent has no order tools.
- Never write `research/universe.md` except through `research-replace.sh`.
- Never emit an empty universe. If the sweep fails, keep last week's and say
  so.
- Never claim the §4 tilts were applied. They were not.
- Never hard-code the working-universe size — read it from `rules.yml` via
  `lib-rules.sh` every run, so a §9-style amendment there takes effect
  without touching this file.
