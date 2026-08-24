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
   - **If a chunk ever comes back inline instead of as a path:** re-issue
     that one chunk once. If it comes back inline again, count it in
     `chunks_failed` (§D) and move on. **Do not try to reconstruct the
     payload by hand** — this agent has no `Write` tool, and retyping ~260 KB
     of JSON-escaped quote text through a shell heredoc is neither reliable
     nor compatible with the "never read a payload" rule that is the whole
     point of this design. A chunk lost to this costs ~150 symbols out of
     ~11,000 for one week; a hand-transcribed payload costs correctness
     silently, which is worse.
   - A chunk that errors is logged and skipped. One bad chunk never stops the
     sweep.
4. **Filter, once, over every payload.** The rank size is
   `working_universe_size` — under the `strategy:` section of `rules.yml`
   (currently **500**<!--rule:strategy_working_universe_size-->) — never
   hard-coded; read it through `lib-rules.sh`:

   **Run this through `bash -c`, not the bare shell.** `lib-rules.sh` uses
   `${!name}` indirection, which zsh rejects with "bad substitution" — and
   the session shell here is zsh. The 2026-08-18 first live run lost time to
   exactly this. The scripts are correct; the invocation is the trap.

   ```bash
   bash -c '
     . scripts/lib-rules.sh && load_rules
     N="$(rule_get strategy_working_universe_size)"
     scripts/universe-filter.sh \
       --payload PATH1 --payload PATH2 ... \
       --qualified-only --rank-top "$N" \
       --out /tmp/universe-ranked.tsv
   '
   ```

   `scripts/universe-filter.sh` guards itself the same way `pre-order-check.sh`
   does — it refuses to run outside bash rather than failing obscurely — so a
   direct call is safe; it is the `. scripts/lib-rules.sh` line above it that
   needs the bash wrapper.

   Exit 0 success. Exit 2 usage — a defect in this command; stop, record in
   §D. Exit 7 — `rules.yml` missing, unreadable, or incomplete: **abort the
   sweep entirely** (per the design doc's failure-mode table, this is not a
   degrade-gracefully case — a caps file the pre-order gate itself can't read
   is a stop-everything condition), keep the existing `research/universe.md`,
   record in §D.

   Read stderr — four possible lines, all of which belong in the §D ledger:

   - `"skipped N unquotable symbol(s)"` when any symbol could not be quoted.
   - `"stub-filtered N symbol(s) under X% session range"` — the takeover-stub
     gate (below). Names are listed; tombstone the confirmed ones.
   - `"N symbol(s) had no usable high/low"` — the stub gate could not be
     evaluated for these and they were **kept**, never rejected.
   - `"ranked N of M (dropped D)"` reporting qualified vs. ranked vs. dropped.

   **The takeover-stub gate.** Under `--qualified-only` the filter also
   rejects any survivor whose latest session high−low range is under
   **0.75**<!--rule:strategy_min_session_range_pct-->% of its price. An
   announced all-cash deal target trades pinned a hair under its deal price,
   and that price is by construction its 52-week high — so the proximity tilt
   this universe *ranks* on was acting as a merger detector. On 2026-08-19,
   nine of fifteen shortlisted names were deal stubs; UTZ and DBRG reached
   WATCH on the artifact and had to be retracted.

   Two things about it that are easy to get wrong:

   - **No data is not zero range.** A payload with no usable high/low reads
     as 0.00% — maximally pinned — and must be kept, not rejected. The rule
     was first specified as `range == 0 AND volume == 0`; that conjunction is
     wrong, because SPY carries `highPrice: 0, lowPrice: 0` alongside 34.4M
     shares of session volume — the most liquid ETF in the market, rejected
     as a merger stub. It fails the same way on any payload that omits the
     two fields entirely. The shipped test is on the high/low fields alone.
   - **The gate has a known escape, and a structural blind spot that no
     threshold closes.** UTZ ranges 0.82% and survives 0.75% — that one is
     a tuning question. **ROKU is not.** Found 2026-08-24: it is a pending
     Fox acquisition at $160.00/share, but the consideration is **mixed —
     $96.00 cash plus 0.9693 FOX Class A shares** — so roughly 40% of the
     deal value floats with the acquirer's stock. A mixed-consideration
     stub therefore keeps a real daily range (ROKU printed 0.85%) and
     **cannot be pinned by construction**. Raising the threshold does not
     catch it; it only starts killing live names. The range test detects
     *all-cash* stubs only, and that is the honest scope of this gate.
     ROKU had sat on `candidates.md` since 2026-08-21 annotated "not
     deal-pinned" — an inference drawn from its range instead of a check
     of its deal status, which is precisely the error the range test
     invites once you trust it too far.

     The threshold itself is validated, not exact: against the 118
     survivors of 2026-08-21 it removed 17 (14.4%) — 8 known stubs, 6
     newly confirmed (FBRX, VREX, SAFT, GBTG, PAYO, SLAB), 2 unconfirmed —
     and **zero live momentum names**. The `session_range_pct` column
     exists so the next escape is visible in `research/universe.md` rather
     than only in hindsight; retune on that evidence, in `rules.yml`, not
     here. **But do not read a passing range as "not a deal target."** The
     proposed complement — an explicit deal-status lookup on the top ~20
     by proximity to 52-week high — is recorded for Chris and NOT
     implemented; it is a different mechanism with an API-budget cost, not
     a tuning of this one.

## §C — Write the working universe

Rewrite `research/universe.md` via `scripts/research-replace.sh universe`.
The script reads the whole file from **stdin**, so **pipe the ranked TSV
into it — never retype the table into a heredoc.** A 500-row table
transcribed by hand is a correctness hazard and would take the payload back
through context, which §E forbids. The working invocation:

````bash
{
  printf '%s\n' \
    '# Fallback universe' \
    '' \
    'This file is never a source for order parameters — every candidate' \
    're-verifies live under §4.9/§4.10. Regenerated weekly by /weekly-universe;' \
    'the daily /deep-research run only reads it.' \
    '' \
    "Assembled: $(date -u +%Y-%m-%dT%H:%M:%SZ) — fetched $FETCHED / quoted $QUOTED /" \
    "qualified $QUALIFIED / ranked $RANKED / dropped $DROPPED. Skipped: ${SKIPPED:--}." \
    '' \
    'The playbook §4 tilts (50-day SMA, 3- and 6-month returns) are NOT applied' \
    'here and belong to the daily tier — the verbose quote payload this pass' \
    'reads carries no price history to compute them from.' \
    '' \
    'Columns: symbol, price (regular-session close), 10-day ADV, dollar volume,' \
    '% from 52-week high, optionable, leverage (a MULTIPLE: 0 single stock,' \
    '1.0 a 1x fund; leveraged and inverse funds are gated out by §3.5 and do' \
    'not appear), last earnings date, ETF flag, latest-session range as a' \
    "% of price (\`-\` = the payload carried no usable high/low)." \
    '' \
    '```'
  cat /tmp/universe-ranked.tsv
  printf '%s\n' '```'
} | scripts/research-replace.sh universe
````

The script enforces the required first line `# Fallback universe` and the
banner text `never a source for order parameters`, both verbatim — get
either wrong and the write is refused (exit 1), not silently corrected. It
also refuses a body of 5 lines or fewer, which catches a truncated pipe.
The body must carry, and the block above produces:

- assembly timestamp and symbol counts (fetched / quoted / qualified / ranked
  / dropped)
- the ranked table verbatim from `/tmp/universe-ranked.tsv` — the ten
  columns `universe-filter.sh` emits: symbol, price, 10-day ADV, dollar
  volume, % from 52-week high, optionable, **leverage**, last earnings date,
  ETF flag, **session_range_pct**
- a plain statement that the playbook §4 tilts (50-day SMA, 3/6-month
  returns) are **not** applied here and belong to the daily tier — the
  quote payload this pass reads has no price-history field to compute them
  from

**On the `leverage` column:** `universe-filter.sh` stores the leverage
**multiple**, not the raw `fundLeverageFactor` the payload carries. Schwab
reports that field as a *percentage* — `0` a single stock, `100.0` a 1x
fund, `200.0`/`300.0` leveraged, negative inverse — and the column divides
it by 100 so §3.5 reasoning reads in multiples (0, 1.0, 3.0, −1.0). The
§3.5 gate keeps only raw 0 and raw 100; a `!= 0` test there would discard
every ETF in the market, roughly half the fetched directory.

## §D — Run ledger

Append one row via
`scripts/data-append.sh events DATE '<json>'`:

```json
{"t":"HH:MM:SS","kind":"weekly_universe","fetched":N,"chunks":N,"chunks_failed":N,
 "quoted":N,"qualified":N,"stub_filtered":N,"nodata":N,"ranked":N,"dropped":N,
 "schwab_calls":N,"skipped":"<or ->"}
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
