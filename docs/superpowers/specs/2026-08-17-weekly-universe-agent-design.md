# Weekly Universe Agent — Design

**Date:** 2026-08-17 (post-close) · **Status:** proposed, awaiting Chris's review
**Governed by:** `CLAUDE.md`. Nothing here amends a manual rule; every § cited
binds exactly as written. This is discovery machinery only — *(strategy rule)*
throughout, changeable without §9.

---

## 1. Problem

Candidate discovery, not rule strictness, is what limits this book. Measured:

| | Current |
|---|---|
| Working universe | 543 names (`research/universe.md`, hand-assembled 2026-08-14 from Wikipedia S&P 500 + 400 lists) |
| Sweep chunk | 50 symbols per postclose run |
| Runs per day | 1 |
| Full sweep | **11 days** |
| Times a name is examined across the 21-session window | **~1.9** |

A name that turns attractive on day 3 may not be looked at again until day 12.

The prior design called this budget-bound — *"unbuildable at ~5 spare chain
calls/day"*. That diagnosis was wrong. The stated budgets say **"ceiling, not
quota"** in three separate places; they were always self-imposed. The real
constraint, confirmed empirically on 2026-08-17, is that **quote payloads land
in agent context**: a 250-symbol batched `get_quotes` returned 51,556
characters and exceeded the tool-result token limit. The 50-symbol chunk exists
to keep payloads readable, not to respect any broker quota.

## 2. Spike findings (2026-08-17, all verified live)

1. **`get_quotes` batches freely.** 250 symbols in one call succeeded. Schwab
   was never the constraint. `universe.md`'s own header records 903 symbols
   quoted at assembly with 0 errors.

2. **`verbose=True` returns the entire qualification pipeline.** Measured at
   **1,723 chars/symbol**, it carries:

   | Field | Serves |
   |---|---|
   | `reference.optionable` | Optionability with **no extra call** |
   | `fundamental.avg10DaysVolume` | Real 10-day ADV |
   | `quote.52WeekHigh` / `52WeekLow` | Playbook §4 proximity tilt |
   | `fundamental.lastEarningsDate` | §3.7 screening at sweep time |
   | `fundamental.nextDivExDate` | The ex-div trap (CSX 8/31 class) |
   | `fundamental.fundLeverageFactor` | Leveraged/inverse ETF identification (§3.5) |
   | `assetMainType` / `assetSubType` | Equity vs ETF |

   Present on **60/60** symbols in a live 60-symbol batch.

   This retires the single-day-`totalVolume`-as-ADV proxy that `universe.md`
   admits to using, and it makes "every optionable equity" free rather than a
   per-symbol lookup.

3. **`fields` is silently ignored.** Passing `fields="quote,fundamental"`
   returned the compact payload unchanged. Same class as the documented
   `get_advanced_option_chain` `strike` bug. **`verbose` is the working
   switch** — add to `schwab-mcp-notes`.

4. **A free, keyless full-market directory exists.** Nasdaq Trader's
   `nasdaqtraded.txt` — HTTP 200, ~1 MB, 13,148 rows, no key. Carries listing
   exchange, ETF flag, and test-issue flag. Filtered to major-exchange,
   non-test, clean symbols: **11,227** (5,653 common, 5,574 ETF).

5. **Script-side filtering works.** A saved 250-symbol payload was parsed and
   reduced to 8 ranked lines by a local script. The agent never read a quote.

## 3. Architecture — two tiers

The split follows the natural frequency of the two jobs: broad discovery is
expensive and slow-changing; tracking is cheap and wants to be daily.

```
  WEEKLY (new)                          DAILY (existing, simplified)
  weekly-universe agent                 deep-research agent
  ├─ Nasdaq directory  →  11,227        ├─ sweep working universe IN FULL
  ├─ verbose sweep, ~45 calls           │    (~300-600 names, 2-3 calls)
  ├─ script filter → qualified          ├─ deep-vet survivors
  └─ writes research/universe.md        └─ candidates.md tiering
     (the week's working universe)
```

Whole market examined **weekly**; working universe examined **daily**. Against
today's 543 names on an 11-day lap.

## 4. The weekly agent

**Definition:** `.claude/agents/weekly-universe.md`, constructed read-only in
the same way as `research-scout` and `deep-research` — **no order tools, no
account tools, no Write/Edit.** Every file write goes through a whitelisted
script. The read-only guarantee is enforced by the harness tool list, not by
instruction.

**Tools:** `Read`, `Glob`, `Grep`, `Bash`, `WebFetch`, `mcp__schwab__get_quotes`,
`mcp__schwab__get_datetime`, `mcp__schwab__get_market_hours`.

**Schedule:** weekly, Sunday, market closed — no time pressure and no
competition with a live session. Registered through the same cron mechanism as
the existing two `/deep-research` entries, and subject to the same session-open
re-creation check (crons are session-scoped; a missing entry is re-created at
open).

**Budget:** ~45 Schwab calls, ~19 MB written to disk, **0 quote payloads into
agent context**. This exceeds the current ~15-call postclose ceiling by design
— that ceiling governs the daily run and is not a broker limit.

## 5. Pipeline

1. **Fetch the directory.** `curl` Nasdaq Trader's `nasdaqtraded.txt` to disk
   via Bash — *not* `WebFetch`, which markdown-converts and summarises rather
   than saving a 1 MB pipe-delimited file verbatim. On failure, keep last week's working universe and record the miss —
   discovery degrades to the prior week, never to nothing.

2. **Pre-filter locally** (no API): major exchange (`N Q A P Z`), `Test Issue =
   N`, symbol ≤ 5 chars, no `$`, and reject instrument-name patterns for
   warrants, rights, units, notes, and preferreds. → ~11,227.

3. **Verbose batched sweep**, 250 symbols/call. Each response exceeds the
   tool-result limit and is written to a file by the harness, which returns the
   path; the agent passes the path to the filter and never reads the payload.
   *Chunk size is chosen partly so responses reliably exceed the inline limit —
   if a chunk ever returns inline, the agent writes it to a temp file itself
   and proceeds identically.* Symbols that fail to quote are logged and
   skipped; a bad symbol must never stall the sweep.

4. **Filter and rank** via `scripts/universe-filter.sh` (new), reading its
   thresholds from `rules.yml` — never hard-coded, per the manual header rule.
   Gates applied, in order:

   - `lastPrice ≥ manual_min_share_price_usd` (§1.4)
   - `avg10DaysVolume × lastPrice ≥ manual_min_avg_daily_dollar_volume` (§1.4)
   - `avg10DaysVolume ≥ manual_min_avg_daily_volume` (sanity floor)
   - `fundLeverageFactor == 0` unless explicitly building the leveraged list
     (§3.5 gates those shut by default)
   - `optionable` recorded, not required — it gates the options track only,
     never the equity sleeves
   - `lastEarningsDate` recorded so §3.7 screening is a lookup, not a call
   - Tilt score from what the payload actually supports: proximity to
     `52WeekHigh`, `netPercentChange`, and dollar liquidity

   **What the weekly tier cannot compute.** The playbook §4 tilt also wants
   *3- and 6-month relative strength* and *above the 50-day SMA*. Neither is in
   the quote payload — both need `get_advanced_price_history`, which is
   per-symbol and therefore unaffordable across 11,227 names. So the tiers
   divide by what the data supports:

   | Tier | Ranks on |
   |---|---|
   | Weekly | Price, real 10-day dollar volume, 52-week proximity, optionable, leverage factor, last earnings date |
   | Daily | The full §4 tilts — 50-day SMA and 3/6-month returns — on the ~300-600 name working universe, where per-symbol history is affordable |

   The weekly tier is a **liquidity-and-eligibility filter that also ranks**, not
   a momentum screen. Calling it a §4 screen would overstate it.

5. **Write the working universe** via `scripts/research-replace.sh universe`,
   preserving the existing required first line and banner (the script enforces
   both). Target **300–600 names** — the daily tier's capacity, not everything
   that passes. Overflow is ranked out, and the count that was dropped is
   recorded rather than silently truncated.

6. **Run ledger** to `status/data/DATE-events.jsonl`: symbols fetched, quoted,
   failed, survivors, dropped-by-rank, elapsed, call count.

## 6. Changes to existing components

| Component | Change |
|---|---|
| `.claude/commands/deep-research.md` §D | Sweep the working universe **in full** each run. Delete the 50-symbol chunking, the `_sweep_cursor` row, and the resume/wrap logic — nothing left to resume |
| `research/universe.md` | Becomes generated output, not a hand-assembled artifact. Weekly refresh-by-requote is replaced by regeneration |
| `get_movers` | Already documented "empirically dead"; formally retired from research use |
| FMP screener | The paywalled screener half is no longer needed. The earnings-calendar half stays useful but is now redundant with `lastEarningsDate` |
| `schwab-mcp-notes` skill | Add: `fields` silently ignored; `verbose` is the switch; verbose ≈ 1,723 chars/symbol |

## 7. Failure modes

| Failure | Behaviour |
|---|---|
| Nasdaq directory unreachable | Keep last week's working universe; record the miss. Never empty |
| Sweep partially completes | Write what qualified; record the unswept remainder. A partial universe is valid, a silent one is not |
| A chunk returns inline instead of file-saved | Agent writes it to a temp file and continues |
| Individual symbols fail to quote | Logged, skipped, sweep continues |
| `rules.yml` unreadable | Abort. `check-consistency.sh` and the gate already fail closed on this; the sweep must too |

## 8. Non-goals

- No change to any manual rule, cap, or gate level.
- No intraday sweeping. The daily tier keeps its cadence; this changes what it
  sweeps, not how often.
- No leveraged/inverse surfacing — §3.5 keeps them gated shut by default.
- No fundamental screening beyond what the quote payload already carries. No
  valuation models, no earnings-quality scoring.
- The working universe is **never a source for order parameters**. Every
  candidate re-verifies live under §4.9/§4.10, exactly as today.

## 9. Open items

1. Confirm the harness reliably file-saves a ~430 KB tool result (observed at
   51 KB and 107 KB; 250-symbol verbose is larger).
2. Decide whether ETFs sweep in the same pass or a second one — 5,574 of the
   11,227 are ETFs, and most will fail the dollar-volume gate.
3. Cron registration mechanics for a weekly cadence, given entries are
   session-scoped and re-created at session open.
4. Whether the 300–600 working-universe target should itself derive from the
   daily tier's measured throughput rather than being a chosen number.
