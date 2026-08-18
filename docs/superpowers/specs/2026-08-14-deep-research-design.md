# Deep-Research System — Design (rev 2, post adversarial review)

**Date:** 2026-08-14 (post-close, post capital amendment `9619eaf`)
**Status:** Approved in conversation by Chris (sections 1–6, 15:11–15:56 PT);
adversarial single-agent review run 16:02–16:07 PT at Chris's direction,
verdict "sound direction, not implementable as written," **all 13 findings
accepted by Chris 16:08 PT** and incorporated in this revision.
**Relationship to existing docs:** extends the research loop
(`docs/superpowers/specs/2026-08-14-research-loop-design.md`,
`.claude/commands/research.md`). Nothing here amends the manual; every rule
cited (§ numbers) binds exactly as written in `CLAUDE.md` v3. Everything in
this document is *(strategy rule)* machinery — discretionary, changeable
without §9 — **except where noted in section 6.4, which adds a
Chris-conversation requirement to strategy-gate changes.**

---

## 1. Problem statement

Day one exposed four research-quality gaps, visible in
`research/candidates.md` itself:

1. **Discovery is anecdotal.** Channels were `get_movers` (empirically dead —
   volume-ranked junk on three separate calls), earnings-calendar sweeps, and
   market-wrap articles. Six passes surfaced one name (GAP) in the §5 price
   band. No systematic screen exists anywhere in the pipeline.
2. **The pipeline over-fishes water the gates predictably empty.** Big
   earnings movers (MNDY, HLIT, SMCI) were repeatedly evaluated and all died
   on the same post-gap ATR ceiling. The qualifying §5 setup — a *modest* gap
   that holds — is precisely what mover lists never surface.
3. **ETFs are unresearched**, despite structurally dodging the earnings gate
   (§3.7 is vacuous for a fund) and offering granularity single names cannot.
4. **No feedback loop on research verdicts.** Pings get counterfactuals;
   tombstones and gate-kills do not, so gate calibration is unmeasurable.

The 2026-08-14 capital amendment (comp capital $900 → ~$2,899.38) widens the
opportunity set materially — unsizeable line $314.78 → ~$1,014.78, options
spot ceiling ~$25–36 → ~$70–105 (widened again to ~$95–140 by the
2026-08-17 cap amendment) — which raises the cost of anecdotal
discovery, and also raises the *scale trap* the review flagged: the widened
bands describe hundreds of names, far more than the call budget can deeply
verify. This design's scope is therefore explicitly **budget-first**: every
feature is sized to what ~30 calls/day can actually do in the **17 sessions**
remaining before the 9/10 lockout.

## 2. Decisions taken in conversation (Chris, 2026-08-14)

- Scope: all four improvement areas (screener, ETF track, scorecard, deeper
  vetting) **plus a dedicated options research track**.
- Architecture: **extend the existing research architecture** (approach A) —
  a sibling agent of research-scout, one candidates file, no split state.
- Execution: **new dedicated agent + two cron entries** (pre-open, post-close
  16:20 ET). The post-close run **takes ownership of the POST window**
  (section 8.1). Pre-open scheduled **8:15 ET** (review finding 10: margin
  over the 5:00 PT wake edge; still >1h before the bell).
- Data: **open to a cheap API**; spike Financial Modeling Prep first,
  Finnhub fallback, **with a mandatory no-API branch** (section 3.3) before
  anything existing is retired.
- Machine is reliably awake by ~5:00 PT.
- **Adversarial review accepted in full** (all 13 findings, Chris 16:08 PT).

## 3. Screener channel

Two screens, two distinct discovery problems. Both run in the post-close
deep pass; output goes to `research/screen/DATE.jsonl` (append-only, via
script). **Only the top 3–5 names per day enter `candidates.md` at WATCH**
(tagged `source: screener`) — the full ranked list lives in the jsonl, so
the candidates file does not bloat (review finding 12; it is 356 lines
after one day).

### 3.1 Universe screen (§4 core/drift profile)

US major exchange; price $5 to the §3.1-derived unsizeable line, with the
§5 $20–60 band flagged; ADV ≥ 1M (§2); above 50-day SMA; positive 3- and
6-month returns; within ~10% of 52-week high. Output: ranked top ~15 to
the jsonl.

**Comp-capital source for a cron-launched agent** (review finding 9): the
agent has no account tools by construction, so the unsizeable line is
derived from the **"State recorded — current" block of the latest
`status/*.md` file** — the same resolution rule tick.md already uses for
HWM — with that figure and its source date echoed into the screen output.
Staleness is bounded by the daily status protocol.

### 3.2 Post-earnings drift screen (§5 catalyst profile)

Names that reported in the **last 1–3 sessions** with a *modest* positive
reaction: report-day move roughly **+2% to +7%**, report-day close in the
top half of its range. Built from the API's earnings calendar joined to
daily bars. This screens *for* the §5 profile instead of stumbling into
its opposite.

### 3.3 Fallback discipline (review finding 6)

`get_movers` is retired from research use **only after the API spike
succeeds**. The spike must also produce a written **no-API branch** before
either cron is installed: a static universe list (hand-assembled once from
free sources, refreshed weekly) swept via batched Schwab `get_quotes`,
with the screener sections marked dormant. If both API providers fail the
spike, the no-API branch *is* the screener until a better source exists —
discovery never regresses below day one.

Screener rows are delayed third-party data. The standing rule applies
verbatim: **never a source for order parameters; every candidate re-verifies
live via Schwab before any promotion, and again under §4.9/§4.10 before any
order.**

## 4. ETF track

Rationale: no earnings print (§3.7 never binds), sector expression without
single-name gap risk, real granularity in the price bands.

- **Universe:** liquid US sector/industry/broad ETFs; price-banded like
  stocks; ADV ≥ 1M. **Leveraged/inverse funds are excluded from this track
  entirely** — §3.5 gates them separately and that gate's default is shut.
- **Qualification:** same §4 trend tilts as stocks (above 50-day, positive
  3/6-month, near 52-week high). The binding gate is expected to be **§3.8
  correlation vs. the held book** (e.g. XLF↔USB, industrials↔CSX), checked
  the same way as the USB↔CSX entry: 60-day return correlation via
  `get_advanced_price_history`.
- **ETF-specific promotion checks:** AUM ≥ ~$500M (closure risk); expense
  ratio recorded; top-10 holdings concentration (a fund that is 40% two
  names is a single-name bet in costume); distribution dates logged so the
  stop-ratchet and stall rules never misread an ex-distribution drop as a
  market move (same trap as CSX's 8/31 ex-div).
- **Synergy:** the track's daily price data doubles as the **sector
  relative-strength ranking** consumed by deeper vetting (section 7).

## 5. Research scorecard

Principle: **every "no" gets scored, not just every ping.**

- At kill time (tombstone or gate-fail), the record gains: the *specific
  gate* that killed it (ATR ceiling, gap-and-hold, price band, size line,
  spread floor, IV/HV, …) and a *hypothetical position* — standard sleeve
  sizing, 10% stop, at the recorded ref price.
- **Mark methodology (review finding 5 — this is the integrity core):**
  marks are computed from **daily OHLC bars**, not closes. A hypothetical
  stop is **hit if the session low ≤ stop level**, filled at
  `min(stop, open)` to model gaps honestly. Close-only marks cannot see
  intraday stop-hits and systematically flatter exactly the high-variance
  names the gates kill — that bias would manufacture "the gate cost money"
  evidence. Both numbers are reported side by side (stop-adjusted and
  close-only) so the bias is visible rather than baked in.
- **Control:** SPY (or the band-universe median) is tracked over the same
  5-session windows. Fifteen shortlisted names drifting up with the index
  proves nothing about the screener.
- Forward-marking horizon: **5 sessions**, then the cohort closes.
  Screener-shortlisted-but-not-taken names get the same treatment.
- **Friday post-close** writes the weekly synthesis to
  `research/scorecard.md`: per-gate hit-rates, ping outcomes, promotion
  outcomes, options vol scoring (section 6.4). Every aggregate prints its
  **n**, with a "sample too small for inference" banner below n = 10.
- **Header rules, verbatim in the file:** (a) the scorecard informs rule
  changes in calm conditions only and never loosens a gate in-flight; a
  gate that "cost" money over a small sample is the §0 pressure,
  quantified — not evidence. (b) **Changes to *(strategy rule)* gates,
  although they do not require §9, require an explicit conversation with
  Chris recorded in a commit** — the scorecard must never become fuel for
  the agent's own layer to quietly soften the strategy (review finding 5
  closed this loophole).

## 6. Options research track

The sleeve's discovery problem is affordability × ladder quality. Current
arithmetic (manual §3.2 as amended 2026-08-17): premium cap ≈ **$580 single /
$870 aggregate** → a Δ≈0.5, ~35-DTE contract is affordable to spot ≈ **$140**
at calm IV, ≈ **$95–100** at realistic IV (method: the measured ~3.8%-of-spot
premium ratio from the 8/14 WMT chain work, scaled). The manual governs these
figures; if they diverge, that is a defect to fix here.

**Scope discipline (review finding 3):** the naive band ($10–140 since the
2026-08-17 amendment; the count below was measured at the older $10–105 band
and is therefore a floor, not a ceiling) is 300–500 names — unbuildable at ~5 spare chain calls/day inside a
17-session window. Everything below is sized to that reality.

1. **Options-viable roster** (`research/options-roster.md`, script-written).
   **Capped at ~20–30 names**: the screener's top-ranked names inside the
   band, plus candidates.md-adjacent underlyings (held names, WATCH, active
   calendar-watch). Each entry carries a **ladder verdict** (does any
   ≥21-DTE, Δ≥0.35 contract clear OI ≥ 500 and spread ≤ 10% of mid?) with a
   timestamp and a **hard TTL of 5 sessions** — an expired verdict is
   *absent*, not stale-but-usable, because spreads and OI move with the IV
   regime (the CSX 50C's 18.9% spread is a snapshot, not a property).
   Chain-checks proceed a few names per day in priority order; the roster
   is useful from day one at partial coverage and complete within ~a week.
2. **IV series — not an IV rank** (review finding 3). Daily ATM IV is
   recorded to `research/iv/` **only for the §B-oi universe (≤ 6
   underlyings)**, whose chains are already being pulled. The file header
   states it is a **series for context, not a rank**: a ~10-point,
   two-week window spanning a single pre-earnings ramp cannot distinguish
   "expensive" from "mechanically ramping into a known event," and
   pretending otherwise is false confidence. §B-opt's existing same-day
   IV/HV ratio remains the honest promotion-time instrument.
3. **Sleeve-live event calendar.** Earnings calendar × roster × guard
   calendar → a forward map of tradeable §6-playbook windows (entry dates,
   flat-by dates, minimum expiry), maintained by the post-close run and
   refreshed pre-open. The hand-derived GAP analysis (entry 8/31+, flat by
   9/4, ≥9/18 expiry), systematized.
4. **Vol scoring.** Every option decision — taken, declined ping, gate-kill
   — is scored on the vol axis in the scorecard: IV paid vs. subsequent
   realized move, and what the post-event crush actually did.

Nothing loosens: §3.2 floors, §B-opt ladder/IV assessment, and the 20%
aggregate cap stand unchanged. This makes the sleeve *ready*, not bigger.

## 7. Pre-open run and deeper vetting

### 7.1 Pre-open run (8:15 ET, weekdays) — **file-only, by construction**

Writes `research/preopen/DATE.md` (script-written), three parts:

- **Earnings digests** for calendar-watch names that printed overnight or
  pre-market: actual vs. consensus, guidance direction, pre-market price and
  volume. The 8/19 TGT/LOW analysis exists before the bell, not during it.
- **Overnight news check on held names** (CSX merger-headline exposure is
  the standing example) and on HOT candidates.
- **Refreshed sleeve-live event calendar** (section 6, item 3).

**Hard constraints (review finding 8):**

- **No §E path from the pre-open run. Zero pings.** At 8:15 ET the "latest
  tick's figures" that §E.2 requires do not exist for the day, settled cash
  has moved overnight, and no §4.5 reconciliation has run — a pre-open ping
  would be evaluated from assumed state (§4.5's named sin) and would burn
  one of §E.5's two daily slots on thin pre-market data, potentially
  rate-limiting the RTH ping that matters.
- **No HOT promotions pre-open.** HOT requires a quote timestamped inside
  regular trading hours. A 4 a.m. print satisfies §C's letter and nothing
  else.
- **The file header states its own limits, verbatim:** "Pre-market data
  informs, it never qualifies. No §5 gate is satisfiable from pre-market
  data (the gates need the report-day RTH session's actual range), and this
  brief is not a source of entry decisions — it is preparation for
  evaluating them live."

### 7.2 Deeper vetting (post-close, promotion-time only)

Fires only on WATCH→HOT promotions plus a weekly sweep of held names:
short interest / days-to-cover, analyst revision direction, sector RS rank
(from the ETF track's data — section 4, no extra calls), and a targeted
news scan for BMY-class overhangs (lawsuits, activist letters, pending
corporate actions — §3.7's checks, searched for rather than waited on).

## 8. Rails, budgets, cron mechanics

### 8.1 POST-window ownership (review finding 1 — was a daily corruption path)

The 16:20 deep run **owns the POST pass**. The same change that installs
the cron **must atomically amend `.claude/commands/research.md` §A.4** to:
the tick-chained intraday loop runs **no research pass after 16:00 ET**;
the POST pass (including §B-oi) belongs exclusively to the deep run.
Without this, the intraday loop fires its own POST pass at ~16:0x (it did,
today, at 16:04) and §B-oi rows are double-written daily, corrupting every
next-morning OI diff. Belt and suspenders: `oi-append.sh` gains an
**idempotency guard** — it refuses a second row for the same
symbol + date, and the deep run skips underlyings already snapshotted today.

### 8.2 Write-path concurrency (review finding 2 — was silent last-writer-wins)

`research-write.sh` gains two guards, and every new full-replacement script
is built with the same pattern plus research-write-style content validation:

- **`flock`** on the target file for the duration of the write.
- **Optimistic concurrency:** the writer passes the `Last pass:` value it
  read at compose time; the script **refuses the write** if the file's
  current value differs, forcing the caller to re-read and merge. A refused
  write is logged, never silently retried with the stale copy.
- **Stamp ownership:** the `Last pass:` line belongs to the **intraday
  loop's cadence gate**. Deep runs and the pre-open run carry it forward
  unchanged and stamp their own activity in their own files (screen jsonl,
  preopen brief) — a deep run must never perturb the 45-minute intraday
  cadence.

### 8.3 Agent construction and write whitelist (review finding 7)

New agent `.claude/agents/deep-research.md`, built like research-scout:
**no order tools, no Write/Edit** — file writes go through scripts only, so
read-only against the broker is harness-enforced. Its command file does
**not** say "inherits §G verbatim" (§G's whitelist names two files and
would contradict this design); it **restates the §G invariants with its own
enlarged whitelist**: `research/candidates.md` (via research-write.sh),
`research/oi/DATE.jsonl` (via oi-append.sh), `research/screen/DATE.jsonl`,
`research/iv/DATE.jsonl`, `research/tombstones.jsonl` (via
research-append.sh), `research/options-roster.md`,
`research/preopen/DATE.md`, `research/scorecard.md` — each via its own
validating script — and nothing else, ever. All other §G lines (single
promotion path, WATCH-before-HOT, never order parameters, never interrupt
the monitoring loop, quiet on failure) carry over unchanged.

### 8.4 Budgets with priority order (review finding 4)

Ceilings: pre-open ~6 Schwab + ~8 API/web; post-close ~15 Schwab + ~15
API/web. The review showed the specced feature set can imply 40–80 calls;
the command file therefore fixes a **hard top-down priority order** (like
§B's "spend it top-down"):

1. §B-oi snapshot (owned here now)
2. Scorecard forward-marks for open cohorts
3. Universe + drift screens
4. Roster chain-checks (a few names/day, TTL-expired first)
5. ETF track refresh
6. Deeper vetting (promotion-triggered)
7. IV series append

Whatever the budget cannot reach is skipped, and **each run logs one line:
`skipped: <features>`** — starvation must be visible in the file, not
discovered in September (review finding 4).

### 8.5 Failure visibility (review findings 11, 13)

- **Failure mode stays quiet, but not invisible:** on any failure the run
  logs to the events corpus and stops — never ALERT.md, never a ping,
  never touching the monitoring loop.
- **Deadman check:** the session-open protocol gains one line — check the
  mtimes of yesterday's `preopen/` and `screen/` outputs; if absent,
  say so in the session-open summary as a status line. A dead research
  loop gets noticed at the next session open, not when someone happens to
  read `research/iv/` a week later.
- **API ledger:** one script-appended line per run; where the provider
  returns rate-limit headers, the ledger records **those**, not just the
  agent's self-count (a miscount otherwise walks silently into the daily
  cap and manifests as finding 11).
- API key lives in the environment, never in the repo.

### 8.6 Cron entries

Two entries, weekdays: **pre-open 8:15 ET** (finding 10: margin over the
wake edge; launchd `StartCalendarInterval` noted as the fire-on-wake
alternative if misses are ever observed), **post-close 16:20 ET**. §A's
gates apply to both runs: halt/restriction → no passes; unacknowledged
ALERT.md → file-only (already true pre-open; suppresses any §E use by the
post-close parent); §8 endgame → no passes from 9/8.

### 8.7 Tombstone growth (review finding 12)

Tombstones move to an append-only file (`research/tombstones.jsonl`,
script-appended) with only a one-line index remaining in candidates.md.
(.jsonl, not .md as rev 2 first wrote: the scorecard consumes gate and
ref_price fields, so the archive is machine-readable; the human index
stays in candidates.md.) The candidates file is rewritten in full up to
8×/day by an LLM carrying content forward; every line it must preserve is
a line it can mutate, so the archive of record moves out of the rewrite
path.

## 9. Non-goals

- No change to any manual rule, gate level, or cap. This is discovery and
  measurement machinery only.
- No intraday expansion: the 45-minute tick-chained pass keeps its current
  shape and budget (HOT refresh + WATCH advance + light scan) — minus the
  POST pass, which moves to the deep run (section 8.1).
- No leveraged/inverse ETF surfacing.
- No "unusual options activity" signal-chasing; §B-oi's skeptical framing
  stands.
- No IV *rank* claims from short self-collected series (section 6.2).

## 10. Open items (implementation phase)

1. **API spike:** verify FMP free-tier reality (screener endpoint, earnings
   calendar, rate limits, data quality); Finnhub as fallback; **build the
   no-API branch regardless** (section 3.3) — it ships before either cron
   is installed. Spike output is a recommendation, not kept code.
2. **Playbook dollar sweep:** the playbook still carries $900-era dollar
   examples. Percentages govern, so nothing is unsafe, but the numbers
   should be updated (strategy-doc edit, no §9 needed).
3. **research.md §A.4 amendment** ships in the same commit as the cron
   installation (section 8.1) — never separately.
4. **Script upgrades** (flock + CAS in research-write.sh, idempotency in
   oi-append.sh, validating writers for the five new paths) land **before**
   the agent that would race them.

## 11. Post-implementation amendment (2026-08-14 evening, Chris-approved): the candidates/standing split

Approved in conversation ("design and execute", 19:25 PT) after the first
live rewrites showed the cost of a monolithic candidates.md: ~360 lines,
full-replacement-rewritten by an LLM up to 8×/day, where every
carried-forward line is a silent-mutation opportunity and pass narrative
rides through every rewrite as ballast.

**Split by lifecycle, not topic:**

1. **`research/candidates.md` — the tiered list only** (~80–120 lines):
   HOT/WATCH entries (thesis, sleeve, checklist state, ref price + ts),
   held-book candidacy notes, the tombstone index line. The ONLY file the
   intraday scout full-rewrites. Header, banner, `Last pass:` semantics,
   CAS, and §C tier rules unchanged.
2. **`research/standing.md` — durable reference, deep-run-only** (new
   `research-replace.sh` target; H1 `# Standing research reference`;
   required banners: the order-parameters banner and a `Verified as of:`
   stamp line): standing screens (unsizeable line, options ceiling,
   channel verdicts), the post-gap ATR decision table and §5 structural
   pre-checklist, options-affordability arithmetic, the calendar map,
   derived-limits table. **Written only by the deep-research run** (§W
   gains it; the scout's whitelist does NOT — the scout reads it and may
   note staleness, never write it). The deep run refreshes the
   `Verified as of:` stamp whenever it rewrites the file and MUST rewrite
   it when any derived number moves (capital, caps, bands).
3. **Pass narrative is retired from both files.** The `PASS` return line,
   events ledger, and screen jsonl already carry it.

**Staleness rail:** the scout's grounding read (§B.1) becomes
"candidates.md + standing.md"; if standing.md's `Verified as of:` is older
than 1 trading session, the scout notes it in its PASS line
(`standing: STALE`) and treats standing-derived numbers as
re-verify-before-use. Mirrors the HOT expiry-stamp rule.

**Files/edits in scope:** research-replace.sh (`standing` target);
research.md §B.1/§C/§G note; research-scout.md; deep-research.md §W + §D
(standing refresh step, priority slot alongside 3); one migration commit
splitting the current file (content-preserving, verified by diff).

## 12. Post-implementation amendment (2026-08-16, Chris-approved): four adoptions from the r/ClaudeAI trading thread

Provenance: r/ClaudeAI thread "I let Claude Code trade stocks with my real
money. Results:" (thread 1voi341; the linked comment describes a
"virtual trading firm" setup). Full evaluation in the approved plan
(~/.claude/plans/is-there-anything-regarding-velvet-reef.md). Everything
here is strategy-layer machinery; no §9 amendment anywhere.

1. **Deterministic pre-order rule checks** — `scripts/pre-order-check.sh`:
   the arithmetic §4.9/§4.10 gates (price floor, §3.1/§3.2/§3.5 caps vs live
   comp capital, settled-cash sufficiency, notional sanity) enforced in code,
   fed explicit arguments, exit 0/nonzero with the tripped gate named. It
   always prints what it did NOT check (earnings, corporate actions, §3.8
   correlation, halts — qualitative gates stay with the operator). Wired into
   the playbook §7 order workflow as mandatory §4.10 automation *(strategy
   rule)*; the manual remains the authority, the script is its calculator.
2. **Macro-event calendar** — the postclose run maintains scheduled macro
   events (FOMC, CPI, PPI, employment; published Fed/BLS calendars via web)
   in standing.md's calendar map as volatility context. Not hard guards;
   guard status stays a Chris conversation.
3. **SEC EDGAR in deeper vetting (§D.6)** — 8-Ks, litigation disclosures,
   Form 4 insider clusters for WATCH→HOT promotions and the weekly held-name
   sweep, with the SEC fair-access User-Agent. Makes the BMY-class overhang
   catch systematic.
4. **Daily-bars spike** — evaluate Stooq (keyless CSV) and yfinance-style
   endpoints as the free daily-bars source for §D.3 tilt computation,
   replacing the weekly-proxy ATR caveat; verdict appended to the screener
   API spike doc. Adopted only if bars match Schwab dailies within tolerance
   on a ~10-symbol sample.

Deliberately NOT taken: sentiment feeds (ApeWisdom/Truth Social — §B-oi
skepticism), other-broker MCPs (§1.7), the quant-zero backtesting fork
(disproportionate to the remaining window).
