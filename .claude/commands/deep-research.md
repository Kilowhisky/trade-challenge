---
description: One deep-research run (preopen | postclose). Dispatches the deep-research agent; owns the POST window (postclose mode).
argument-hint: "preopen | postclose"
---

# /deep-research — the deep pass (design: 2026-08-14-deep-research-design.md rev 2)

The deep pass is the once-a-day systematic half of research — screener
sweep, ETF track, scorecard forward-marks, options roster, IV series,
deeper vetting — as distinct from `/research`'s 45-minute tick-chained
scan. It runs on a schedule (cron: 8:15 ET preopen, 16:20 ET postclose,
design rev2 §8.6), never on an ad hoc mid-session request — that is what
`/research` is for. Qualification rules (playbook §4/§5/§6, manual §1.4,
§2, §3.2, §3.7) are unchanged and unrestated here; this file governs
discovery mechanics and budget only.

## §Dispatch

The parent does not execute §P/§D itself:

1. Parent checks §A. If any gate closes the run, stop here and output
   nothing beyond the gate reason.
2. Parent dispatches `.claude/agents/deep-research.md` **in the
   background** with cached context: date + ET time, held symbols +
   sectors, comp capital SOURCE = the "State recorded — current" block of
   the latest `status/*.md` (echo the figure and its date — the exact
   resolution rule tick.md §B5 uses for HWM), active calendar guards
   (NVDA week 8/24–8/28, endgame dates), mode (`$ARGUMENTS`: preopen |
   postclose).
3. When the agent's result arrives, parent outputs **Line 1 verbatim** to
   Chris:
   `DEEP <mode> <ET time> | screened n | roster n/M | cohorts n | skipped: <features or ->`
   A `HOT-FRESH:` line (postclose only) feeds `research.md`'s **§E ping
   gate**, run by the parent exactly as it would for a `research-scout`
   pass — the deep run does not reimplement pinging. A `FAIL:` line is
   logged (§8.5) and never escalated on its own.
4. Fallback + failure handling as `research.md` §Dispatch: first, a
   `general-purpose` subagent prompted to obey `deep-research.md`; then
   inline §P/§D as a last resort. Two consecutive genuine failures: log an
   events corpus entry and go quiet for the session — a missed deep pass
   is never `ALERT.md` material and never interrupts the monitoring loop.
5. Cron entries are session-scoped (harness CronCreate: in-memory, die with
   the session, 7-day auto-expiry). At session open, if the two entries are
   absent, re-create them: `15 5 * * 1-5` → `/deep-research preopen` and
   `20 13 * * 1-5` → `/deep-research postclose` (PT local).

## §A — Preconditions

1. Mode required; unknown mode = no-op.
2. Halt / restriction / cash call (latest tick or status): no runs.
3. Unacknowledged `ALERT.md`: run is file-only (postclose parent §E
   suppressed; preopen is file-only always, §P).
4. §8 endgame: from 9/8, no runs. Nothing this pass could surface is
   enterable inside the lockout horizon.
5. postclose only: if `research/oi/DATE.jsonl` already has today's §B-oi
   rows **and** `research/screen/DATE.jsonl` exists, today's run already
   happened — no-op. (Belt-and-suspenders on top of `oi-append.sh`'s own
   exit-4 idempotency guard, design rev2 §8.1.)

## §P — preopen mode (8:15 ET; FILE-ONLY, design rev2 §7.1)

Budget ceiling: ~6 Schwab + ~8 API/web — a ceiling, never a quota; most
runs should use far less. This mode has **no path to an
order, a ping, or a HOT promotion** — it exists to prepare the RTH session
for live evaluation, not to make a decision ahead of it.

- **No pings, no §E, no HOT promotions, no `candidates.md` writes.** At
  8:15 ET the latest tick's figures §E.2 needs do not exist yet, settled
  cash has moved overnight, and no §4.5 reconciliation has run — evaluating
  a ping now would be exactly the assumed-state sin §4.5 exists to forbid,
  and could burn one of §E.5's two daily ping slots on thin pre-market data.
  HOT requires a quote timestamped inside regular trading hours; a 4 a.m.
  print satisfies §C's letter and nothing else.
- Writes **exactly one file**: `research/preopen/DATE.md`, via
  `scripts/research-replace.sh preopen DATE`.
- Content, in order:
  1. **Earnings digests** for calendar-watch names that printed overnight
     or pre-market: actual vs. consensus, guidance direction, pre-market
     price and volume.
  2. **Overnight news** on held names (merger-headline exposure is the
     standing example) and on HOT candidates.
  3. **Refreshed sleeve-live event calendar** — earnings calendar ×
     options-roster × guard calendar, the forward map of tradeable §6
     playbook windows (entry dates, flat-by dates, minimum expiry).
- The file header carries verbatim: **"Pre-market data informs, it never
  qualifies. No §5 gate is satisfiable from pre-market data (the gates
  need the report-day RTH session's actual range), and this brief is not
  a source of entry decisions — it is preparation for evaluating them
  live."** `research-replace.sh preopen` only checks the first sentence
  (`"Pre-market data informs, it never qualifies"`) before refusing the
  write — the parenthetical and the rest of the sentence are mandated by
  the spec regardless of what the script validates, and must not be
  dropped just because a shorter version would still pass.
- Required first line (H1): `# Pre-open brief — DATE` (em dash), exactly
  — `research-replace.sh preopen` refuses any other first line with
  exit 1, and under the quiet-failure rule (§W) that refusal would
  silently lose the brief for the day rather than surface loudly.
- Ends by logging one events-corpus line via `scripts/data-append.sh
  events DATE JSON`, the same shape §D uses below:
  `{"t":"HH:MM:SS","event":"deep_research","mode":"preopen","skipped":"<features or ->","api_calls":N,"schwab_calls":N}` —
  recording what this file-only run swept and what it could not reach.

## §D — postclose mode (16:20 ET; owns the POST window, design rev2 §8.1)

Budget ceiling: ~15 Schwab + ~15 API/web — a ceiling, never a quota; most
runs should use far less. This run **owns the POST window
in its entirety** — the tick-chained intraday loop no longer runs a
research pass after 16:00 ET (`research.md` §A.4), so §B-oi and the
post-close candidate sweep happen exactly once, here, never twice.
Budget is spent only on the §D priority features. A suspected
provider-capability change (new endpoint, paywall shift, schema change) is
logged to the events corpus as an owed item and probed in a supervised
session — never inside a run.

**HARD PRIORITY ORDER — spend top-down, log what the budget never
reached:**

1. **§B-oi snapshot** (per `research.md` §B-oi: held underlyings + HOT/
   WATCH names with an options angle, cap 6 underlyings, one chain call
   each, bounded per-contract rows). `oi-append.sh` exit 4 = that
   underlying is already snapshotted today — not an error, skip it
   silently and move to the next.
2. **Scorecard maintenance.** Rewrite `research/scorecard.md` via
   `scripts/research-replace.sh scorecard` (required first line `#
   Research scorecard`; required banner text `never loosens a gate
   in-flight` and `explicit conversation with Chris` — both verbatim,
   enforced by the script). Three things happen here, in order:
   a. **Cohort ingestion (what opens a cohort).** Before marking
      anything, pull in what needs a cohort but doesn't have one yet:
      every name in the most recent not-yet-ingested screener shortlist
      (`research/screen/DATE.jsonl`'s ranked top ~15 from §D.3 below —
      not just the 3–5 promoted to `candidates.md` — excluding
      `_sweep_cursor` machinery rows, §D.3) and every row appended to
      `research/tombstones.jsonl` since the scorecard was last written.
      Each opens one entry in `## Open cohorts` with a **hypothetical
      position** — standard sleeve sizing, a §3.4 ATR-scaled stop, at the recorded
      ref price — dated today. Because this step runs before §D.3's
      sweep in the priority order, "most recent" in practice usually
      means the **prior run's** shortlist; today's own §D.3/§D.6 output
      is ingested on the *next* run. A one-run delay opening a cohort is
      immaterial against the 5-session forward-marking horizon.
      Tombstone writes (`scripts/research-append.sh tombstones DATE
      JSON`, required fields `symbol`, `date`, `gate`, `reason`,
      `ref_price`, plus optional `hypo_qty`/`hypo_stop` once scored)
      happen whenever this run rejects a name at a named gate — a
      screener survivor failing a §4/§5 tilt, a roster candidate failing
      the ladder check (§D.4), or a deeper-vetting overhang (§D.6) — and
      are ingested the same way, on whichever run next reaches this step.
   b. **Forward-marking.** For each name already in `## Open cohorts`,
      one `get_advanced_price_history` (daily bars since the cohort
      opened). Mark methodology (spec §5, do not soften): a hypothetical
      stop is **HIT if the session low ≤ stop**, filled at
      **`min(stop, open)`** to model gaps honestly — never mark from the
      close alone, which flatters exactly the high-variance names the
      gates killed. Report stop-adjusted and close-only figures side by
      side. **SPY once per run** as the control window.
   c. **Closing.** A cohort reaching its 5-session forward-marking
      horizon moves from `## Open cohorts` to `## Closed cohorts`.
   **Fridays only:** also write `## Weekly synthesis` — per-gate
   hit-rates, ping outcomes, promotion outcomes, options vol scoring
   (§6.4) — every aggregate printing its **n**, with a "sample too small
   for inference" banner below n = 10.
3. **Universe + drift screens.** Append ranked rows to
   `research/screen/DATE.jsonl` via `scripts/research-append.sh screen
   DATE JSON` (required fields: `symbol`, `t`, `src`); only the
   **top 3–5** — combined with §D.5's ETF-track qualifiers below, one
   shared daily WATCH-entry cap, not two stacked caps — enter
   `research/candidates.md` at WATCH tagged `source: screener` (or
   `source: etf-track`, §D.5), via `scripts/research-write.sh
   --expect-last-pass 'Last pass: <full line read at compose time>'`.
   - **Universe screen (no working free-tier screener API — spike verdict
     2026-08-14):** FMP's screener endpoint is paywalled on both the
     legacy `v3` and current `stable` generations, on the free tier. This
     is **not** the §3.3 retirement condition firing — retirement
     requires the API spike to *succeed*, and the screener half of it
     FAILED — so `get_movers` is simply **not used by this run's
     channels**: the batched-quote universe sweep is the better available
     channel regardless of retirement status. The screen runs as a
     **batched `get_quotes` sweep of `research/universe.md`** (543 names)
     in ~50-symbol chunks against this run's budget. A full sweep of the
     list spans **multiple postclose runs** — say so plainly in
     `skipped:` when the sweep doesn't finish.
     **Resume via a cursor row, never the last survivor seen** — a
     zero-survivor chunk must still advance the sweep, or it re-sweeps
     the same dead chunk forever. Every sweep run appends one machinery
     row to today's `research/screen/DATE.jsonl` via
     `scripts/research-append.sh screen DATE JSON`:
     `{"symbol":"_sweep_cursor","t":"HH:MM:SS","src":"universe_sweep","last_index":N,"swept":M}`
     (validates fine — `research-append.sh` only requires `symbol`/`t`/
     `src`). `swept` (M) is the count of symbols **processed through the
     qualification filter** this chunk — quoted-but-unfiltered symbols
     are NOT counted and the cursor does not advance past them — never
     the count of qualifying survivors — a chunk that yields **zero
     survivors still writes its full `swept` count** and the cursor
     advances exactly as far as a chunk that ranked fifteen.
     Resume point = read the **most recent** `_sweep_cursor` row across
     `research/screen/*.jsonl` and continue from `last_index + swept`,
     wrapping to 0 past the end of `universe.md`.
     `_sweep_cursor` rows are sweep machinery, **never candidates** —
     exclude them from ranking, from any `candidates.md` promotion, and
     from §D.2a's cohort ingestion. **Prioritize the §5 granularity band and
     the options-affordability band first** within each chunk — the
     names most likely to qualify are swept before the long tail.
     Qualification against survivors: price $5 to the §3.1-derived
     unsizeable line, ADV ≥ 1M, above 50-day SMA, positive 3- and
     6-month returns, within ~10% of 52-week high. Rank the top ~15
     survivors of the chunk into the jsonl.
     **Daily bars for tilt math (spike verdict 2026-08-17, in the
     screener-api-spike doc):** for the ranked shortlist survivors only
     (~15/day, never the whole sweep), daily bars may come from Yahoo's
     v8 chart endpoint — with retry-after-backoff (first attempts
     reliably 429), and a bar-count/date-set cross-check against one
     Schwab series before trusting any derived ATR/SMA (Yahoo dropped a
     bar in testing). On any mismatch or endpoint change, fall back to
     Schwab daily bars for names that survive to full measurement, and
     weekly-proxy figures stay clearly labeled as such. Schwab remains
     the source of record; Stooq is rejected (bot-walled).
     **Weekly refresh (Fridays only, after the day's chunk):** re-fetch
     the S&P 500/400 constituent lists, re-quote for price/ADV, and
     rewrite `research/universe.md` via `scripts/research-replace.sh
     universe` (required first line `# Fallback universe`, required
     banner text `never a source for order parameters`, both verbatim,
     enforced by the script). A refreshed list may reorder symbols; the
     sweep cursor's `last_index` is best-effort continuity across a
     refresh, not an exact guarantee — an occasional re-swept or skipped
     name at the refresh boundary is an acceptable cost of a
     hand-assembled weekly list, not a defect to chase.
   - **Post-earnings drift screen:** FMP's `stable/earnings-calendar`
     endpoint (env var `FMP_API_KEY`, sourced from `.env.local` at the
     repo root — reference the variable name only, never a key value)
     **does** work on the free tier and is the drift screen's source,
     joined to daily bars. Filter: reported in the **last 1–3 sessions**,
     report-day move **+2% to +7%**, report-day close in the top half of
     its range. The endpoint carries **no report-time (bmo/amc) field**
     (spike finding) — get before/after timing from the same web confirm
     already run for calendar-watch names; where that isn't available,
     record `report_time: unknown` and treat the name conservatively
     rather than guess.
   - **Sleeve-live event calendar (spec §6 item 3) is not written here.**
     §D does not produce that file directly — it is a section of
     `research/preopen/DATE.md`, composed fresh each morning by §P from
     the earnings calendar (this drift screen's data), the options
     roster (§D.4), and the guard calendar. §D's job is limited to
     keeping the roster and screen data current enough for §P's
     derivation to be accurate; nothing in this run writes the calendar
     itself.
   - Screener rows are delayed third-party data, never a source for order
     parameters — every candidate re-verifies live via Schwab before any
     promotion, and again under §4.9/§4.10 before any order.
4. **Roster chain-checks.** A few names/day, **TTL-expired first** (a
   ladder verdict older than 5 sessions is *absent*, not stale-but-usable
   — spreads and OI move with the IV regime). Roster is capped at **~20–30
   names**: the screener's top-ranked names inside the options
   affordability band, plus candidates.md-adjacent underlyings (held,
   WATCH, active calendar-watch). Each entry: ladder verdict (does any
   contract at the §3.2 DTE floor and the playbook's delta floor clear OI ≥ 500
   and spread ≤ 10% of mid? — read the current numbers from `rules.yml`) with a
   timestamp. Rewrite `research/options-roster.md` via
   `scripts/research-replace.sh roster` (required first line `# Options-
   viable roster`; required banner text `never a source for order
   parameters` and `TTL`, verbatim, enforced by the script).
5. **ETF track refresh.** Same §4 trend tilts as stocks (above 50-day
   SMA, positive 3-/6-month, near 52-week high). Binding gate is expected
   to be **§3.8 correlation vs. the held book** — 60-day return
   correlation via `get_advanced_price_history`, same method as any
   single-name correlation check. ETF-specific checks: AUM ≥ ~$500M,
   expense ratio recorded, top-10 holdings concentration, distribution
   dates logged (so the stop-ratchet and stall rules never misread an
   ex-distribution drop as a market move). **Leveraged/inverse funds are
   NEVER surfaced** by this track — not watched-and-rejected, simply
   absent from every output, full stop; §3.5 gates them separately and
   that gate's default is shut. This track's daily price data doubles as
   the sector relative-strength ranking §D.6 consumes, at no extra call
   cost. **Output:** every ETF observation, qualifying or not, is
   recorded to `research/screen/DATE.jsonl` via `research-append.sh
   screen` (`src: etf-track`); **qualifying ETFs enter
   `research/candidates.md` at WATCH tagged `source: etf-track`**, under
   the **same combined top 3–5 per day cap** as §D.3's screener names
   (§D.3) — the two channels share one daily WATCH-entry budget, they
   don't stack.
6. **Deeper vetting** — only if a WATCH→HOT promotion is pending, plus a
   weekly sweep of held names: short interest / days-to-cover, analyst
   revision direction, sector RS rank (from §D.5's ETF data, no extra
   calls), targeted news scan for overhang risk (lawsuits, activist
   letters, pending corporate actions — §3.7's checks, searched for
   rather than waited on). **SEC EDGAR** (spec §12 adoption 3) joins
   these sources for the same two triggers — a pending WATCH→HOT
   promotion and the weekly held-name sweep, never a standalone pass:
   recent **8-Ks** (material events), **litigation disclosures**, and
   **Form 4 insider-transaction clusters** for the name, via EDGAR's JSON
   APIs (`data.sec.gov/submissions/CIK##########.json`, full-text search,
   `browse-edgar`). Every request carries a proper identifying
   **User-Agent** per SEC's fair-access policy (a real
   name/contact-string, never a generic or absent one) — the class of
   overhang this catches systematically is exactly the BMY-class CVR
   litigation revival that a momentum screen alone missed on day one
   (`status/2026-08-14.md`).
7. **IV series append.** `research/iv/DATE.jsonl` via
   `scripts/research-append.sh iv DATE JSON` (required fields: `symbol`,
   `t`, `atm_iv`) — **§B-oi universe only, ≤ 6 underlyings**, whose chains
   this run already pulled in step 1 (no extra calls). This is a
   **series for context, not a rank**: a ~10-point, two-week window
   cannot distinguish "expensive" from "mechanically ramping into a known
   event." §B-opt's same-day IV/HV ratio remains the honest promotion-
   time instrument; the IV series never substitutes for it.
8. **Standing refresh (design rev2 §11).** Runs last, after steps 1–7 have
   had the chance to move a derived number. **Rewrite
   `research/standing.md`** via `scripts/research-replace.sh standing`
   (required first line `# Standing research reference`; required banner
   text `never a source for order parameters`, verbatim; a separately-
   enforced, anchored `^Verified as of:` stamp line — a banner-substring
   match alone is not enough, the script refuses a write whose stamp line
   is missing even if the phrase appears elsewhere in prose) **whenever
   any derived number moved this run** — competition capital, §3.1/§3.2
   caps, sleeve bands, ATR baselines, the calendar map. **Macro-calendar
   maintenance** (spec §12 adoption 2) is part of the calendar map: this
   step keeps scheduled macro events — FOMC, CPI, PPI, employment (the
   monthly jobs report) — current from published Fed (FOMC meeting
   calendar) and BLS (CPI/PPI/ employment release schedules) calendars,
   sourced via the web budget. These entries are **volatility context
   alongside the earnings/guard calendar, not hard guards** — neither
   CLAUDE.md nor the playbook treats a macro date as a trading
   restriction; whether one ever should is a Chris conversation, not
   decided by this run. Refresh the `Verified as of:` stamp on every
   rewrite this step performs. A run that touched nothing standing-
   derived may leave the file alone — but if leaving it alone would let
   the stamp go stale (**older than 1 trading session** by the next
   scheduled run), rewrite anyway, after re-verifying the numbers rather
   than just re-stamping stale ones. The scout reads this file
   (`research.md` §B.1) and never writes it — this step is the file's
   only write path. **This step spends no budget in its rewrite-only form
   and is never skipped for budget; only its re-verify branch costs
   calls.**

**Every run ends by logging one events-corpus line** via
`scripts/data-append.sh events DATE JSON`:

```json
{"t":"HH:MM:SS","event":"deep_research","mode":"postclose",
 "skipped":"<features or ->","api_calls":N,"schwab_calls":N}
```

(§P's file-only run logs the same shape with `"mode":"preopen"` instead
— see §P above; the `mode` field is never hardcoded to one value across
the two run types), plus provider rate-limit headers **when present**. The FMP spike
(2026-08-14) observed **no `x-ratelimit-*` headers on any probed
endpoint** — the published free-tier ceiling (250 requests/day) is
doc-sourced, not header-sourced. Until a header is actually seen, the
ledger line records the run's own `api_calls` self-count and notes
`headers: absent` rather than fabricating a quota reading; the first run
that does see a rate-limit header should supersede this note.

**CAS refusal from `research-write.sh` (exit 3)** on the candidates.md
write: re-read the file, merge the WATCH additions onto the fresh copy,
retry **once**. A second refusal: log it and skip the `candidates.md`
write for this run — the jsonl rows are already durable, nothing is lost.

## §W — Write whitelist

Restates §G (design rev2 §8.3); the agent has Write/Edit withheld and
these scripts are the ONLY write paths.

- `research/candidates.md` — `scripts/research-write.sh
  --expect-last-pass`; carry the `Last pass:` line forward **unchanged**
  — that stamp belongs to the intraday cadence gate, never to a deep run.
- `research/oi/DATE.jsonl` — `scripts/oi-append.sh`
- `research/screen/DATE.jsonl`, `research/iv/DATE.jsonl`,
  `research/tombstones.jsonl` — `scripts/research-append.sh`
- `research/options-roster.md`, `research/preopen/DATE.md`,
  `research/scorecard.md`, `research/universe.md` — `scripts/research-replace.sh`
- `research/standing.md` — `scripts/research-replace.sh standing`
  (deep-run-only, design rev2 §11; see §D.8 for the refresh trigger). The
  scout reads this file but **must never write it** — its only write path
  is this run.
- **Run-ledger exception:** `status/data/DATE-events.jsonl` —
  `scripts/data-append.sh events` — the one events-corpus write every run
  makes (§P and §D both), outside the `research/` tree. Named here
  explicitly because §D mandates this write and it would otherwise read
  as forbidden by "nothing else, ever" below.

Nothing else, ever. All other §G lines carry over unchanged: single
promotion path, WATCH-before-HOT (never HOT in the pass a name was first
found; HOT requires an RTH-timestamped quote), never a source of order
parameters, never interrupt the monitoring loop, quiet on failure — log
to the events corpus and stop; never `ALERT.md`, never a ping on failure.

The session-open **deadman check** (mtimes of yesterday's `preopen/` and
`screen/` outputs, flagged in the session-open summary if absent) lives in
the session-open protocol, not here — that is a separate task's territory
(design rev2 §8.5).
