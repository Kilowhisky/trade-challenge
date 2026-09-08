---
description: One deep-research run (preopen | postclose). Dispatches the deep-research agent; owns the POST window (postclose mode).
argument-hint: "preopen | postclose"
---

# /deep-research — the deep pass (design: 2026-08-14-deep-research-design.md rev 2)

The deep pass is the once-a-day systematic half of research — screener
sweep, ETF track, scorecard forward-marks, options roster, IV series,
deeper vetting — as distinct from `/research`'s 45-minute tick-chained
scan. It runs on a schedule (engine cron: 08:17 ET preopen, 16:22 ET
postclose, design rev2 §8.6), never on an ad hoc mid-session request —
that is what `/research` is for. Qualification rules (playbook §4/§5/§6,
manual §1.4, §2, §3.2, §3.7) are unchanged and unrestated here; this file
governs discovery mechanics and budget only.

Scheduled: 08:17 ET (preopen) / 16:22 ET (postclose).

## §Dispatch

The parent does not execute §P/§D itself:

1. Parent checks §A. If any gate closes the run, stop here and output
   nothing beyond the gate reason.
2. Parent dispatches `.claude/agents/deep-research.md` **in the
   background** with cached context: date + ET time, held symbols +
   sectors, ACCOUNT VALUE SOURCE = the "State recorded — current" block of
   the latest status document (echo the figure and its date — the exact
   resolution rule tick.md §B5 uses for HWM), resolved via
   `mcp__engine__status_latest()`. Then active calendar guards
   (**read them from playbook §4 and §10 at dispatch time — never from a
   date hard-coded here**), mode (`$ARGUMENTS`: preopen | postclose).

   This line used to name "NVDA week 8/24-8/28". That was the guard's
   pre-amendment form and it went stale the moment playbook §4 narrowed
   the window to **8/25-8/27** and made it correlation-conditional rather
   than a blanket freeze — so a dispatch obeying this file would have
   applied a wider, superseded guard and suppressed uncorrelated names
   the playbook explicitly leaves tradeable. Caught 2026-08-24 at
   dispatch. **A calendar date written into a command file is a date that
   silently stops matching its rule**; the guard's dates live in the
   playbook, which is the only place they are amended.
3. When the agent's result arrives, parent outputs the `summary` field
   verbatim to Chris:
   `DEEP <mode> <ET time> | screened n | roster n/M | cohorts n | skipped: <features or ->`
   A `hot_fresh` entry (postclose only) feeds `research.md`'s **§E ping
   gate**, run by the parent exactly as it would for a `research-scout`
   pass — the deep run does not reimplement pinging. A failed run is
   logged (§8.5) and never escalated on its own.
4. Fallback + failure handling as `research.md` §Dispatch: first, a
   `general-purpose` subagent prompted to obey `deep-research.md`; then
   inline §P/§D as a last resort. Two consecutive genuine failures: log an
   events ledger entry and go quiet for the session — a missed deep pass
   is never alert-worthy and never interrupts the monitoring loop.
5. **The schedule lives in the engine, not in the session.** Both runs are
   fired by the engine's own scheduler — 08:17 ET preopen and 16:22 ET
   postclose, computed in America/New_York directly. Every run is one
   engine job: the engine owns the lock, the ET window guard, the
   heartbeat and the Discord relay.

   There is nothing left to re-create at session open. This used to read
   "cron entries are session-scoped (harness CronCreate: in-memory, die with
   the session, 7-day auto-expiry)… at session open, if the two entries are
   absent, re-create them." That mitigation could not work and the record shows
   it: re-creation prevents *tomorrow's* miss, never today's, and the machine
   was a sleeping laptop that could not fire at 05:15 PT on a closed lid at
   all. Four consecutive briefs were lost 8/15–8/19 and more after.

   The playbook §9 pre-open catch-up **stays**, and is still the guarantee —
   it now covers a container that was down or a job that fired outside its
   08:00–09:15 ET window, rather than a session that simply started late.
   The engine's job-deadman check reports a miss to Discord the same morning.

## §A — Preconditions

1. Mode required; unknown mode = no-op.
2. Halt / restriction / cash call (`mcp__engine__status_latest().last_tick
   .level` or the latest status document): no runs.
3. Unacknowledged alert (`mcp__engine__alert_read()`): run is file-only
   (postclose parent §E suppressed; preopen is file-only always, §P).
5. postclose only: if `mcp__engine__ledger_read(name="oi", date=today)`
   already has today's §B-oi rows **and**
   `mcp__engine__ledger_read(name="screen", date=today)` has rows, today's
   run already happened — no-op. (Belt-and-suspenders on top of
   `ledger_append`'s own idempotency guard, design rev2 §8.1.)
6. preopen only: if `mcp__engine__doc_read(kind="preopen", date=today)
   .exists` is true, today's brief has already been written — no-op. The
   08:17 ET job and the session-open catch-up (playbook §9) both target
   that one document, and exactly one of them should ever write it.

## §P — preopen mode (08:17 ET **or** session-open catch-up; FILE-ONLY, design rev2 §7.1)

Budget ceiling: ~6 engine/broker reads + ~8 API/web — a ceiling, never a
quota; most runs should use far less. This mode has **no path to an
order, a ping, or a HOT promotion** — it exists to prepare the RTH session
for live evaluation, not to make a decision ahead of it.

- **No pings, no §E, no HOT promotions, no `candidates` writes.** At
  08:17 ET the latest tick's figures §E.2 needs do not exist yet, settled
  cash has moved overnight, and no §4.5 reconciliation has run — evaluating
  a ping now would be exactly the assumed-state sin §4.5 exists to forbid,
  and could burn one of §E.5's two daily ping slots on thin pre-market data.
  HOT requires a quote timestamped inside regular trading hours; a 4 a.m.
  print satisfies §C's letter and nothing else.
- Writes **exactly one document**: the `preopen` document for today, via
  `mcp__engine__doc_write(kind="preopen", date=today, body=…)`.
- **Two triggers, one output.** The 08:17 ET job is the fast path; the
  playbook §9 session-open catch-up is the guarantee. The catch-up fires
  only when the job did not (no document for today, §A.6) and only **before
  12:00 ET**. A catch-up run is otherwise **identical** — same document,
  same banner, same prohibitions, same budget. It additionally stamps, on
  the line immediately below the H1:
  `**Catch-up run — the 08:17 ET job did not fire. Swept at HH:MM ET.**`
  so that nothing downstream reads a 10:20 ET brief as an 08:17 one. The
  prohibitions carry even though one of their two stated reasons has
  expired by catch-up time: §4.5 *has* reconciled by then, but the §E.5
  ping slots still belong to the live `/research` loop, and a file-only
  brief must not spend them.
- Content, in order:
  1. **Earnings digests** for calendar-watch names that printed overnight
     or pre-market: actual vs. consensus, guidance direction, pre-market
     price and volume.
  2. **Overnight news** on held names (merger-headline exposure is the
     standing example) and on HOT candidates.
  3. **Refreshed sleeve-live event calendar** — earnings calendar ×
     options-roster × guard calendar, the forward map of tradeable §6
     playbook windows (entry dates, flat-by dates, minimum expiry).
- The document header carries verbatim: **"Pre-market data informs, it never
  qualifies. No §5 gate is satisfiable from pre-market data (the gates
  need the report-day RTH session's actual range), and this brief is not
  a source of entry decisions — it is preparation for evaluating them
  live."** `mcp__engine__doc_write(kind="preopen")` only checks the first
  sentence (`"Pre-market data informs, it never qualifies"`) before
  refusing the write — the parenthetical and the rest of the sentence are
  mandated by the spec regardless of what the tool validates, and must not
  be dropped just because a shorter version would still pass.
- Required first line (H1): `# Pre-open brief — DATE` (em dash), exactly
  — `doc_write(kind="preopen")` refuses any other first line, and under
  the quiet-failure rule (§W) that refusal would silently lose the brief
  for the day rather than surface loudly.
- Ends by logging one events-ledger line via
  `mcp__engine__ledger_append(name="events", date=today, record=JSON)`, the
  same shape §D uses below:
  `{"t":"HH:MM:SS","event":"deep_research","mode":"preopen","catchup":true|false,"skipped":"<features or ->","api_calls":N,"schwab_calls":N}` —
  recording what this file-only run swept and what it could not reach.
  This ledger write is available in the postclose job's tool grant; a
  preopen run that cannot reach `ledger_append` records the same facts in
  `notes` on its returned verdict instead.

## §D — postclose mode (16:22 ET; owns the POST window, design rev2 §8.1)

Budget ceiling: ~15 engine/broker reads + ~15 API/web — a ceiling, never a
quota; most runs should use far less. This run **owns the POST window
in its entirety** — the tick-chained intraday loop no longer runs a
research pass after 16:00 ET (`research.md` §A.4), so §B-oi and the
post-close candidate sweep happen exactly once, here, never twice.
Budget is spent only on the §D priority features. A suspected
provider-capability change (new endpoint, paywall shift, schema change) is
logged to the events ledger as an owed item and probed in a supervised
session — never inside a run.

**HARD PRIORITY ORDER — spend top-down, log what the budget never
reached:**

1. **§B-oi snapshot** (per `research.md` §B-oi: held underlyings + HOT/
   WATCH names with an options angle, cap 6 underlyings, one chain call
   each, bounded per-contract rows). `mcp__engine__ledger_append(name=
   "oi", …)` returning `appended: false` means that underlying is already
   snapshotted today — not an error, skip it silently and move to the
   next.
2. **Scorecard maintenance.** Rewrite the `scorecard` document via
   `mcp__engine__doc_write(kind="scorecard", body=…)` (required first line
   `# Research scorecard`; required banner text `never loosens a gate
   in-flight` and `explicit conversation with Chris` — both verbatim,
   enforced by the tool). Three things happen here, in order:
   a. **Cohort ingestion (what opens a cohort).** Before marking
      anything, pull in what needs a cohort but doesn't have one yet:
      every name in the most recent not-yet-ingested screener shortlist
      (`mcp__engine__ledger_read(name="screen", …)`'s ranked top ~15 from
      §D.3 below — not just the 3–5 promoted to `candidates` — and every
      row appended to the tombstones ledger since the scorecard was last
      written (`mcp__engine__ledger_read(name="tombstones", …)`). Each
      opens one entry in `## Open cohorts` with a **hypothetical
      position** — standard sleeve sizing, a §3.4 ATR-scaled stop, at the
      recorded ref price — dated today. Because this step runs before
      §D.3's sweep in the priority order, "most recent" in practice usually
      means the **prior run's** shortlist; today's own §D.3/§D.6 output
      is ingested on the *next* run. A one-run delay opening a cohort is
      immaterial against the 5-session forward-marking horizon.
      Tombstone writes (`mcp__engine__tombstone(symbol, date, gate,
      reason, ref_price[, hypo_qty, hypo_stop])`) happen whenever this run
      rejects a name at a named gate — a screener survivor failing a
      §4/§5 tilt, a roster candidate failing the ladder check (§D.4), or a
      deeper-vetting overhang (§D.6) — and are ingested the same way, on
      whichever run next reaches this step.
   b. **Forward-marking.** For each name already in `## Open cohorts`,
      one `mcp__engine__price_history` call (daily bars since the cohort
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
3. **Universe + drift screens.** Append ranked rows via
   `mcp__engine__ledger_append(name="screen", date=today, record=JSON)`
   (required fields: `symbol`, `t`, `src`); only the **top 3–5** —
   combined with §D.5's ETF-track qualifiers below, one shared daily
   WATCH-entry cap, not two stacked caps — enter the `candidates`
   document at WATCH tagged `source: screener` (or `source: etf-track`,
   §D.5), via `mcp__engine__doc_write(kind="candidates",
   expect_last_pass="Last pass: <full line read at compose time>",
   body=…)`.
   - **Universe screen (no working free-tier screener API — spike verdict
     2026-08-14):** FMP's screener endpoint is paywalled on both the
     legacy `v3` and current `stable` generations, on the free tier. This
     is **not** the §3.3 retirement condition firing — retirement
     requires the API spike to *succeed*, and the screener half of it
     FAILED — so `mcp__engine__movers` is simply **not used by this run's
     channels**: the batched-quote universe sweep is the better available
     channel regardless of retirement status. The screen runs as a
     **batched `mcp__engine__quotes` sweep of the working universe in
     full, every run**.
     **Mechanics:**
     1. **Symbols** come from `mcp__engine__universe_symbols()` — the
        working universe as a flat, sorted list of qualified symbols, one
        call, no payload to route around. Never re-derive the list from
        prose or from a stale copy.
     2. **Batch the quotes** in chunks of up to 50 symbols (the
        `mcp__engine__quotes` per-call cap) and qualify each against the
        floors below.

     The working universe is maintained weekly by the engine's own
     universe job (design rev2's `/weekly-universe` is retired — Task 14)
     and is already reduced to names clearing the §1.4 floors, so it is
     small enough to sweep completely every day. There is no cursor and no
     resume — the sweep either completes or reports what it missed.

     Qualification against survivors: the §1.4 liquidity floors
     (`mcp__engine__rules` if available, else `rules.yml`) for price and
     volume, up to the §3.1-derived unsizeable line, above 50-day SMA,
     positive 3- and 6-month returns, within ~10% of 52-week high. Rank
     the top ~15 survivors of the sweep into the ledger.
     **Daily bars for tilt math (spike verdict 2026-08-17, in the
     screener-api-spike doc):** for the ranked shortlist survivors only
     (~15/day, never the whole sweep), daily bars may come from Yahoo's
     v8 chart endpoint — with retry-after-backoff (first attempts
     reliably 429), and a bar-count/date-set cross-check against one
     `mcp__engine__price_history` series before trusting any derived
     ATR/SMA (Yahoo dropped a bar in testing). On any mismatch or endpoint
     change, fall back to `mcp__engine__price_history` for names that
     survive to full measurement, and weekly-proxy figures stay clearly
     labeled as such. The engine's own price history remains the source
     of record; Stooq is rejected (bot-walled).
     The working universe is regenerated weekly by the engine's own job;
     this daily run only reads it via `mcp__engine__universe_symbols()`.
   - **Post-earnings drift screen:** FMP's `stable/earnings-calendar`
     endpoint (env var `FMP_API_KEY`, sourced from the runner's own
     environment — reference the variable name only, never a key value)
     **does** work on the free tier and is the drift screen's source,
     joined to daily bars. Filter: reported in the **last 1–3 sessions**,
     report-day move **+2% to +7%**, report-day close in the top half of
     its range. The endpoint carries **no report-time (bmo/amc) field**
     (spike finding) — get before/after timing from the same web confirm
     already run for calendar-watch names; where that isn't available,
     record `report_time: unknown` and treat the name conservatively
     rather than guess.
   - **Sleeve-live event calendar (spec §6 item 3) is not written here.**
     §D does not produce that document directly — it is a section of the
     `preopen` document, composed fresh each morning by §P from the
     earnings calendar (this drift screen's data), the options roster
     (§D.4), and the guard calendar. §D's job is limited to keeping the
     roster and screen data current enough for §P's derivation to be
     accurate; nothing in this run writes the calendar itself.
   - Screener rows are delayed third-party data, never a source for order
     parameters — every candidate re-verifies live via `mcp__engine__quotes`
     before any promotion, and again under §4.9/§4.10 before any order.
4. **Roster chain-checks.** A few names/day, **TTL-expired first** (a
   ladder verdict older than 5 sessions is *absent*, not stale-but-usable
   — spreads and OI move with the IV regime). Roster is capped at **~20–30
   names**: the screener's top-ranked names inside the options
   affordability band, plus candidates-adjacent underlyings (held, WATCH,
   active calendar-watch). Each entry: ladder verdict (does any
   contract at the §3.2 DTE floor and the playbook's delta floor clear OI ≥ 500
   and spread ≤ 10% of mid? — read the current numbers from
   `mcp__engine__rules`) with a timestamp. Rewrite the `options-roster`
   document via `mcp__engine__doc_write(kind="options-roster", body=…)`
   (required first line `# Options-viable roster`; required banner text
   `never a source for order parameters` and `TTL`, verbatim, enforced by
   the tool).
5. **ETF track refresh.** Same §4 trend tilts as stocks (above 50-day
   SMA, positive 3-/6-month, near 52-week high). Binding gate is expected
   to be **§3.8 correlation vs. the held book** — 60-day return
   correlation via `mcp__engine__price_history`, same method as any
   single-name correlation check. ETF-specific checks: AUM ≥ ~$500M,
   expense ratio recorded, top-10 holdings concentration, distribution
   dates logged (so the stop-ratchet and stall rules never misread an
   ex-distribution drop as a market move). **Leveraged/inverse funds are
   NEVER surfaced** by this track — not watched-and-rejected, simply
   absent from every output, full stop; §3.5 gates them separately and
   that gate's default is shut. This track's daily price data doubles as
   the sector relative-strength ranking §D.6 consumes, at no extra call
   cost. **Output:** every ETF observation, qualifying or not, is
   recorded via `mcp__engine__ledger_append(name="screen", …)`
   (`src: etf-track`); **qualifying ETFs enter the `candidates` document
   at WATCH tagged `source: etf-track`**, under the **same combined top
   3–5 per day cap** as §D.3's screener names (§D.3) — the two channels
   share one daily WATCH-entry budget, they don't stack.
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
7. **IV series append.** `mcp__engine__ledger_append(name="iv", date=
   today, record=JSON)` (required fields: `symbol`, `t`, `atm_iv`) —
   **§B-oi universe only, ≤ 6 underlyings**, whose chains this run already
   pulled in step 1 (no extra calls). This is a **series for context, not
   a rank**: a ~10-point, two-week window cannot distinguish "expensive"
   from "mechanically ramping into a known event." §B-opt's same-day
   IV/HV ratio remains the honest promotion-time instrument; the IV series
   never substitutes for it.
8. **Standing refresh (design rev2 §11).** Runs last, after steps 1–7 have
   had the chance to move a derived number. **Rewrite the `standing`
   document** via `mcp__engine__doc_write(kind="standing", body=…)`
   (required first line `# Standing research reference`; required banner
   text `never a source for order parameters`, verbatim; a separately-
   enforced, anchored `^Verified as of:` stamp line — a banner-substring
   match alone is not enough, the tool refuses a write whose stamp line
   is missing even if the phrase appears elsewhere in prose) **whenever
   any derived number moved this run** — account value, §3.1/§3.2
   caps, sleeve bands, ATR baselines, the calendar map. **Macro-calendar
   maintenance** (spec §12 adoption 2) is part of the calendar map: this
   step keeps scheduled macro events — FOMC, CPI, PPI, employment (the
   monthly jobs report) — current from published Fed (FOMC meeting
   calendar) and BLS (CPI/PPI/ employment release schedules) calendars,
   sourced via the web budget. These entries are **volatility context
   alongside the earnings/guard calendar, not hard guards** — neither
   CLAUDE.md nor the playbook treats a macro date as a trading
   restriction; whether one ever should is a Chris conversation, not
   decided by this run. Refresh the `verified_as_of` stamp on every
   rewrite this step performs. A run that touched nothing standing-
   derived may leave the document alone — but if leaving it alone would
   let the stamp go stale (**older than 1 trading session** by the next
   scheduled run), rewrite anyway, after re-verifying the numbers rather
   than just re-stamping stale ones. The scout reads this document
   (`research.md` §B.1) and never writes it — this step is the document's
   only write path. **This step spends no budget in its rewrite-only form
   and is never skipped for budget; only its re-verify branch costs
   calls.**

**Every run ends by logging one events-ledger line** via
`mcp__engine__ledger_append(name="events", date=today, record=JSON)`:

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

**A `DocCasMismatch` refusal from `doc_write` (kind="candidates")** on the
candidates write: re-read the document, merge the WATCH additions onto the
fresh copy, retry **once**. A second refusal: log it and skip the
candidates write for this run — the ledger rows are already durable,
nothing is lost.

## §W — Write whitelist

Restates §G (design rev2 §8.3); the agent has no write surface except the
engine's document and ledger tools, and these are the ONLY calls that
change anything:

| Target | Tool |
| --- | --- |
| `candidates` document | `mcp__engine__doc_write(kind="candidates", expect_last_pass=…)` — carry the `Last pass:` value forward **unchanged**; that stamp belongs to the intraday cadence gate, never to a deep run. |
| OI ledger | `mcp__engine__ledger_append(name="oi", …)` |
| screen / iv / tombstones ledgers | `mcp__engine__ledger_append(name="screen"\|"iv"\|"tombstones", …)`; tombstones specifically via `mcp__engine__tombstone(...)` |
| `options-roster`, `preopen`, `scorecard` documents | `mcp__engine__doc_write(kind=…)` |
| `standing` document | `mcp__engine__doc_write(kind="standing")` (deep-run-only, design rev2 §11; see §D.8 for the refresh trigger). The scout reads this document but **must never write it** — its only write path is this run. |
| events ledger | `mcp__engine__ledger_append(name="events", …)` — the one events line every run makes (§P and §D both). |

**The `universe` document is NOT on this list.** It is a valid document
kind (the write tool accepts it), but it belongs to the engine's own
weekly universe job (Task 14) and is only ever read here, via
`mcp__engine__universe_symbols()`. Two tiers holding a write path to the
same document is how the weekly sweep's output gets clobbered by a daily
run mid-week.

Nothing else, ever. All other §G lines carry over unchanged: single
promotion path, WATCH-before-HOT (never HOT in the pass a name was first
found; HOT requires an RTH-timestamped quote), never a source of order
parameters, never interrupt the monitoring loop, quiet on failure — log
to the events ledger and stop; never an alert, never a ping on failure.

The session-open **deadman check** (freshness of yesterday's `preopen` and
`screen` outputs, flagged in the session-open summary if absent) lives in
the session-open protocol, not here — that is a separate task's territory
(design rev2 §8.5). Note the division of labour, since the two look
similar: the deadman **detects** that yesterday's loop died and reports it;
the §9 pre-open catch-up **repairs** today's missing brief. Neither
substitutes for the other, and the deadman is deliberately still quiet —
it never raises an alert.
