---
description: One research pass — maintain the tiered candidates list (research/candidates.md). The unit of the research loop.
argument-hint: "[focus for this pass, optional — e.g. a symbol or sector to evaluate]"
---

# /research — one pass of the research loop

A research pass is a **read-only** sweep of the market against the
playbook's qualification rules (§4 core, §5 catalyst, §6/§3.2 options),
maintaining `research/candidates.md`. It answers one question: *if capital
frees up or a setup ripens, what would we even look at?*

A research pass never places, previews, replaces, or cancels an order, and
never opens the entry workflow itself. Its only outputs are the candidates
file and, through the parent's §E gate, at most a one-line ping.

Design + decisions: `docs/superpowers/specs/2026-08-14-research-loop-design.md`.

Run chained after the monitoring tick (`/loop 5m "/tick then /research"`) —
§A's cadence gate makes the chained call a no-op most cycles — or standalone
when Chris asks for a research pass directly.

## §Dispatch — run the pass in the `research-scout` subagent

The parent does **not** execute §B–§D itself:

1. Parent checks §A. If any gate closes the pass, stop here — that is the
   normal outcome for ~8 of every 9 chained invocations (the cadence gate).
2. Parent dispatches **`research-scout`** (`.claude/agents/research-scout.md`)
   **in the background** — the tick cadence must never block on research.
   The dispatch prompt supplies cached context: date + ET time, held symbols
   and their sectors, competition capital and drawdown level, settled cash,
   any active calendar guard (NVDA week 8/24–8/28, endgame dates), and
   `$ARGUMENTS` if this pass was run with a stated focus.
3. When the scout's result arrives, parent runs §E (the ping gate) on any
   `HOT-FRESH:` lines. Pinging never happens inside the subagent.

Fallback chain on a `FAIL:` about missing tools, same as tick.md §Dispatch:
`general-purpose` subagent prompted to obey `research-scout.md`; then inline
§B–§D as a last resort. Two consecutive genuine failures: log an events
corpus entry and stop chaining research passes for the session — research is
optional machinery; **never** let it generate `ALERT.md` noise or interrupt
the monitoring loop.

---

## §A — Preconditions (parent-side, cheap — file reads only)

1. **Cadence gate:** read the `Last pass:` line of `research/candidates.md`.
   If it is less than **45 minutes** old (ET), the pass is not due — stop,
   output nothing. A missing file or missing line means a pass is due.
2. **Halt / restriction / cash call** (from the latest tick): no passes. An
   account that cannot buy has no use for entry candidates.
3. **Unacknowledged `ALERT.md`:** the pass may run (research is read-only)
   but §E is **suppressed** — no pings in closing-only posture.
4. **After 16:00 ET: no research passes from the tick-chained loop.** The
   POST pass — including §B-oi — is owned exclusively by the 16:20 ET
   deep-research run (/deep-research postclose; design rev2 §8.1). A
   chained invocation after 16:00 simply stops here.
5. **§8 endgame:** from 9/8 onward, no passes. Nothing researched can be
   entered inside the lockout horizon.

## §B — The sweep (scout-side)

Budget: **~8 Schwab calls + ~4 web fetches** per pass — a ceiling, never a
quota; most passes should use far less. Spend it top-down:

1. **Ground.** Read `research/candidates.md` + `research/standing.md`, the
   playbook §4/§5/§6, and the manual's §1.4/§2/§3.2/§3.7 floors. Re-read the
   rules every pass. **Staleness rail:** if standing.md's `Verified as of:`
   stamp is older than 1 trading session, append `standing: STALE` to this
   pass's `PASS` return line (§D) and treat every standing-derived number
   (sleeve caps, the unsizeable line, ATR baselines, the calendar map) as
   re-verify-before-use rather than as ground truth — mirrors the HOT
   expiry-stamp rule.
2. **Refresh HOT.** Quote every HOT candidate (one `get_quotes` call).
   Re-check each against its written checklist at the live price. Anything
   that no longer passes, or whose verification is older than 1 trading
   session, demotes to WATCH with the reason noted.
3. **Advance WATCH.** For the most promising WATCH names, fill in what is
   missing to qualify — earnings date and result via web, post-gap ATR via
   `get_advanced_price_history`, option floors via the chain tools. Promote
   to HOT only with the full checklist written out (§C).
4. **Scan for new.** `get_movers` and/or a targeted web sweep (earnings
   reactions in the last 1–3 sessions, sector relative strength). New ideas
   enter at WATCH — **never** straight to HOT in the same pass they were
   found; a promotion needs its data verified, and haste is the tell of a
   bad candidate. Check tombstones before researching any name.
5. If `$ARGUMENTS` names a focus, it takes the budget's priority after
   step 2 (HOT freshness is never skipped).

### §B-opt — Ladder/IV assessment (mandatory before an option goes HOT)

The §3.2 floors check the *contract*; this checks the *chain* (spec §8.1).
One `get_advanced_option_chain` read, assessed and written into the HOT
checklist:

- **IV context:** contract IV vs the underlying's ~20-day realized vol
  (from `get_advanced_price_history`, usually already pulled for the ATR
  ceiling). We only ever buy premium — an IV spike means paying the top and
  eating reversion even when the direction is right. **IV/HV well above
  ~1.3 defaults to reject**; promoting anyway requires the written thesis
  to justify paying up.
- **Ladder health:** bid/ask spread quality across ±3 strikes around the
  candidate, OI distribution (a lone-strike OI island is a worse ladder
  than the same OI spread across neighbors), and day volume.
- **Exit realism:** one line on what selling this contract looks like —
  the §3.3 close at 5 DTE is a *sale into this ladder*, and a thin ladder
  is a bad exit fill.

### §B-oi — Post-close OI snapshot and diff (POST pass only, spec §8.2) (now executed only inside /deep-research postclose)

Open interest updates once daily (OCC overnight) — this never runs intraday.

1. **Universe:** held underlyings + HOT/WATCH names with an options angle.
   Cap **6 underlyings** (budget: one chain call each, on top of the normal
   pass budget).
2. **Snapshot:** per underlying, append one compact record to
   `research/oi/DATE.jsonl` via `scripts/oi-append.sh` — spot, aggregate
   call/put OI, and per-contract rows bounded to strikes within ±20% of
   spot, ≤ 60 DTE, OI ≥ 100, cap ~40 contracts (schema in the script
   header).
3. **Diff** against the most recent prior `research/oi/*.jsonl`. Notable:
   a contract's OI up **≥ 30% and ≥ 500 contracts**, or a marked aggregate
   put/call shift. First run has no prior file — write the baseline, no
   diff, done.
4. **Disposition:** a notable delta may put the **underlying** on WATCH,
   with the observation and its size recorded — never straight to HOT,
   never a ping, never a trade trigger. Frame skeptically in the file: in
   our liquid large-cap universe most OI flow is hedging, spread legs, or
   market-maker positioning, and §3.7 forbids the earnings plays that
   classic "unusual activity" chasing points at. It is an idea source, not
   a signal.

## §C — Tier rules

- **HOT** — every playbook gate passes as of this pass, with the checklist
  written line by line in the file: §1.4/§2 floors; sleeve-specific rules
  (§5 catalyst qualification incl. post-gap ATR ceiling and price band; §4
  tilts + sector carve-out; §3.2 option quality floors **plus the §B-opt
  ladder/IV assessment for option candidates**); **earnings date verified
  with source and date** (§3.7); corporate-actions check; sector vs. held
  names (§3.8); reference price with quote timestamp; expiry stamp
  (auto-demote if not re-verified within 1 trading session).
- **WATCH** — thesis one-liner, target sleeve, what is missing to qualify,
  reference price + timestamp.
- **Tombstones** — name, date, disqualifying reason. Revisit only if the
  stated disqualifier has changed (a new quarter, a corporate action
  completing, a price band re-entered).
- The file header carries, verbatim: **"This file is never a source for
  order parameters — every entry re-verifies live under §4.9/§4.10."**
- Durable reference material (standing screens, ATR gate table, options
  arithmetic, calendar map) lives in `research/standing.md`, not here —
  see §D for the write boundary.

## §D — Write and return (scout-side)

Full-replacement write via `scripts/research-write.sh --expect-last-pass
'<the Last pass: line read at compose time>'` (stdin heredoc; carry
forward everything not changed this pass; update `Last pass:` to now, ET;
on exit-3 refusal: re-read, merge, retry once). Then return the
machine-consumed lines specified in
`.claude/agents/research-scout.md` — one `PASS` summary line, `HOT-FRESH:`
lines only for candidates newly verified HOT *this pass*, nothing else.

## §E — The ping gate (parent-side)

A ping fires only when **all** hold:

1. A `HOT-FRESH:` line arrived from this pass.
2. Deployable capacity exists: settled cash covers a minimum viable
   position for that sleeve, sleeve cap has room, correlation not blocking
   (§3.8) — judged from the latest tick's figures.
3. Not at §3.6 **Halt** — structurally guaranteed, since Halt stops the loop
   upstream (§A.2), so this gate cannot bind here. **Halt is the only
   drawdown level**; there is no intermediate band that restricts an
   instrument, and none may be inferred.
4. No calendar guard active for adds (NVDA week 8/24–8/28; endgame per §A.5).
5. That symbol has not pinged today, and today's ping count is **< 2**
   (count `"ping"` events in today's events corpus before emitting).

The ping is **one line** to Chris: symbol, sleeve, thesis, reference price.
Log it: `scripts/data-append.sh events DATE '{"t":"HH:MM:SS","event":"ping",
"symbol":"XYZ","sleeve":"catalyst","ref_price":0.00}'`. A ping is an
invitation to run the full §4.9/§4.10 entry discipline — which may, and
often should, conclude "no." Record the outcome (acted / declined + reason)
in the decisions corpus; a declined ping gets a counterfactual entry so the
ping mechanism itself is scored by mid-window.

A suppressed or rate-limited would-be ping is not lost — the candidate is
in the file, which the session protocol reads at open and after any exit.

## §F — Stop conditions

The research loop inherits the monitoring loop's life: chained after
`/tick`, it stops when the tick loop stops (§G of tick.md), and additionally
per §A.4 (no research passes after 16:00 ET — the POST pass belongs to
/deep-research postclose) and §A.5 (endgame). Flat book + stopped tick
loop = no research passes either; run one manually at the next session open
if wanted.

## §G — Never, in a research pass

- Never place, preview, replace, or cancel an order — the scout has no
  order tools; the parent never opens the entry workflow from inside the
  research path (a ping's entry evaluation is a new, deliberate action
  under the full order discipline).
- Never write any file except `research/candidates.md` (via
  `research-write.sh`) and `research/oi/DATE.jsonl` (via `oi-append.sh`,
  POST pass only). **Never write `research/standing.md`** — the scout reads
  it (§B.1) and may note staleness, but it is deep-run-only (§W of
  `deep-research.md`); this whitelist is unchanged by the candidates/
  standing split.
- Never promote to HOT without the written checklist, and never in the same
  pass a name was first found.
- Never promote an option candidate to HOT without the §B-opt ladder/IV
  assessment, and never treat an OI delta as more than a WATCH-tier idea.
- Never treat the candidates file as a source of order parameters.
- Never ping past the rate limit or under suppression — and never convert
  a ping into pressure: **"zero qualified setups is a legitimate outcome"**
  (playbook §4).
- Never let research machinery interrupt the monitoring loop — on repeated
  failure it goes quiet, not loud.
