# Competition Strategy Design

**The playbook.** How the account is actually traded, inside the box that
`CLAUDE.md` defines. Required reading at every session open.

**Governed by `CLAUDE.md`. Where this document and the manual disagree, the
manual wins** — and the disagreement is a defect to fix, not a choice to make.
Rules here stricter than the manual are marked *(strategy rule)*:
discretionary, changeable without a §9 amendment.

Origins: a four-agent adversarial review (`2026-08-13-adversarial-review.md`)
and a five-agent comparative research pass
(`2026-08-13-comparative-research.md`). Revision history in `CHANGELOG.md`.

---

## 1. Goal and scoring — stated honestly

- Winner-take-all against a small field of **unconstrained, long-biased**
  traders (live intel: margin, fast call-flipping, NVDA 220/225 calls held
  into the confirmed 2026-08-26 print).
- **Score = account value at the 2026-09-14 close − $900.00 reserve.**
  Mark-to-market; nothing is liquidated for scoring; the account continues.
- **The trade we have knowingly made:** the manual's box caps our right tail
  far below an unconstrained book's. Estimated P(first) ≈ 5–25% — the top of
  that range only because the field looks correlated-long, making our real
  win path "*the AI-complex trade has a bad month and we preserve capital
  through it*." What the box buys is near-zero probability of destroying real
  money, and Chris has chosen that explicitly. This document optimizes
  P(first) **subject to** the manual — it does not pretend rank or
  "finishing well" is the objective, and it does not pretend the constraints
  are free. The month's dominant event (NVDA 8/26) is one we are structurally
  excluded from playing directly; that is the headline cost of the box.
- Marks are honest by construction: §1.4/§2 liquidity floors mean scoring
  prices are realizable prices.
- **Horizon correction (Chris, 2026-08-13, after the review):** 9/14 is a
  **checkpoint, not a terminus** — the game is friendly, repeated, and
  intended to run long-term, with the account persisting. This inverts the
  review's one-shot game theory: in a repeated game, the unconstrained
  field's variance must survive every checkpoint while a disciplined book
  compounds through each one it doesn't. The P(first-at-this-checkpoint)
  estimate above stands; the strategy's true objective is the sequence.
  Competition-scoped rules (§8 lockout, scoring, the §3.2 option caps) get
  revisited per §9 at the checkpoint — this month runs as agreed with the
  field.

## 2. Operating constraints (verified 2026-08-13)

| Constraint | Consequence |
|---|---|
| Execution autonomous. MCP gate flipped 2026-08-14; place/cancel round-trip drill passed the same session. `replace_order` is absent from the tool surface — a stop amendment is cancel + re-place, counted per CLAUDE.md §4.10 | I place orders under §6 |
| Stop rejected until entry fills (verified live) | Fill→stop window real; naked-position watch is the loop's top alert |
| T+1 cash account, zero GFV tolerance | $900 reserve as float; invariant in §3; field semantics gated (§7.6) |
| Token expires every 7 days | **Re-auth: 8/19, 8/26, 9/2, and 9/8 pre-market** (9/8 token outlives the 9/14 bell) |
| Loop coverage honestly ≈ 5–15% of the trading day, ≈0% at open/close | Operative deadline everywhere = **session end**, never "market close"; Chris does a 5-min 9:30 ET check any day a position is open |
| §3.2 per-position cap: 20% of comp capital, $580 at $2,899 | Options on roughly sub-$140 underlyings at calm IV, ~$95–100 at realistic IV; ceiling rises as the account grows |
| Earnings calendar (verified): HD 8/18, TGT+LOW 8/19, WMT 8/20, NVDA+CRM 8/26, DLTR 8/27, AVGO 9/3 | Supply themes are consumer-retail and tech, clustered 8/18–9/3; desert after ~9/4 |

## 3. Capital architecture

**Competition capital = account value − $900.00 reserve.**

**The reserve invariant** *(strategy rule, replaces the old bridge wording)*:
**total cash (settled + unsettled) ≥ $900.00 at all times.** Equivalently:
position exposure at marks never exceeds competition capital. A bridged buy is
capped at min(planned size, incoming unsettled proceeds); same-day proceeds
qualify. This forbids the leak where a stopped-out loss plus full-size
redeployment quietly eats the reserve: **redeploys are capped at actual
proceeds, not at original position size.**

| Sleeve | Ceiling | At $2,899.38 | Positions |
|---|---|---|---|
| Core | 50% | $1,450 | No name count *(strategy rule)*; per-position §3.1/§3.8 bind |
| Catalyst | 30% | $870 | 1, occasionally 2 |
| Options | 30% open / 20% per position (§3.2, scales with the account) | $870 open / $580 per position | No count limit |
| Leveraged ETF | 20% aggregate, within rows above | $580 | Gated (§6) |
| **Max deployed** | **100%** | **$2,899.38** | sleeves now sum to 110% — they are individual ceilings; the 100% total-deployment line and the reserve invariant bind first |

- **Sleeve compliance is checked at order time only**, against competition
  capital at current marks. Mark drift never forces a sale and never frees
  option capacity. "Open premium" (§3.2) = premium **paid** on open
  positions; marks irrelevant.
- **§3.6 thresholds ratchet:** Caution = 0.88 × HWM, Halt = 0.80 × HWM of
  competition value. HWM resolution — source, once-per-session caching, and
  the no-intraday-ratchet rule — is defined in tick.md §B5, one place.
  (At HWM $2,900: $2,552/$2,320. At HWM $3,190: $2,807.20/$2,552.)
- **At Caution:** halved ceilings gate **new buys only** — existing positions
  are never force-sold by Caution; all open options are closed, **accepting
  losses** (pinned reading of §3.6). No new options.
- **§3.8 method** *(strategy rule)*: evaluated **at entry** — sector/theme
  classification plus trailing 60-day daily-return correlation (>0.7 =
  correlated). Mid-hold correlation convergence in a selloff is not a
  violation but **blocks all adds** to the correlated cluster. Weekly
  correlation check in the monitoring table.
- Deployment pace: core days 1–2, catalyst on qualified setups only. Partial
  deployment is legitimate (§3.9).

## 4. Core sleeve — selection

- Universe: large/mid caps and sector ETFs passing §1.4/§2 floors, **no
  earnings scheduled before 9/14** (verified at entry, §3.7).
- **Sector carve-out** *(strategy rule, from the sequencing finding)*: both
  core names come from **outside consumer-retail and mega-cap tech** —
  industrials, healthcare, financials, energy relative-strength leaders —
  because those two themes are the window's entire catalyst supply and §3.8
  would otherwise let the blind 8/14–17 core deployment empty the catalyst
  sleeve for the month.
- Tilt: 3–6 month relative strength, uptrend, proximity to 52-week high
  (the tilt with live large-cap evidence at this horizon).
- Volatility ceiling *(strategy rule)*: reject if 10% ÷ daily ATR% < 3.
  **For catalyst entries, ATR is measured on the post-gap series** — pre-gap
  ATR systematically passes names whose post-print volatility makes a 10%
  stop a coin flip *(comparative research #2)*.
- **NVDA-week guard (8/24–8/28)** *(strategy rule)*: no adds and no new
  positions while the field's correlated event (NVDA 8/26) resolves —
  breadth is narrow, margin debt is at a 2000/2007/2021-signature record,
  and our win path is surviving the field's event, not joining it.
  De-grossing and anti-field expressions remain permitted. Expect a muted
  tape to produce few qualified catalyst setups generally (Q4-25: under half
  of beats saw a positive next-day move); **zero qualified setups is a
  legitimate outcome.**

## 5. Catalyst sleeve — post-earnings momentum continuation

Relabeled honestly: academic PEAD is dead in our forced (liquid, large-cap)
universe, and this window cannot reach the day-20+ horizon where residual
drift is claimed. What the qualification rules actually select — a
high-turnover large cap gapping to near its 52-week high on a clean beat —
carries a **short-term momentum continuation** premium with current,
replicated large-cap evidence. Same trades; correct theory; success judged
accordingly (§12). Entry remains **after** the print, never through it
(§3.7 + IV/gap math).

**Qualification (all required):** reported within last 1–3 sessions; clean
beat, ideally raised guidance; **gapped up and held** (closed report day in
top half of range); §1.4/§2 floors; sector distinct from both core names
(§3.8 — guaranteed possible by §4's carve-out); next report ~3 months out.
Prefer the **~$20–60 price band** *(strategy rule — whole-share granularity
and stop slippage at $870 size)*.

**Entry:** sessions 1–3 post-report, limit at/inside ask, day-only, and only
in the first half of a session Chris intends to keep open *(strategy rule)*.

**Exits — first trigger wins (no time exit; it clipped the only payoff):**
- §3.4 stop-limit, GTC, trigger 10% below **share-weighted average entry**,
  limit 5% below trigger; re-computed via single replace on each add
- Stall rule: two consecutive closes below blended entry, evaluated from
  session 4 onward (entry day = session 1) → close next session
- Stop ratchet — **active** (§3.4: the trigger is a floor, may be raised,
  never lowered): +8% → breakeven, +15% → entry+8%, limit
  always 5% below trigger, single-message replace only, §4.6 applies to
  replaces.
- Endgame calendar (§10)

## 6. Options and leveraged ETFs

**Options are a standalone sleeve** (Chris, 2026-08-13: "You should be free
to plan options at any point"). Any directional thesis — long calls or long
puts, catalyst-linked, anti-field, or independent — may be expressed in
options, provided the position carries its own written §4.9 rationale before
the order.

What binds, from the manual:

| Rule | Constraint |
|---|---|
| §3.2 quality floors | ≥21 DTE · Δ≥0.35 · OI≥500 · spread ≤10% of mid |
| §3.2 caps | ≤20% of competition capital per position (**$580**), ≤30% open (**$870**). No position-count limit, no cumulative budget; premium still logged per §7.2 |
| §3.3 | Each open position carries its own expiration clock into the monitoring table |
| §3.7 | No option held through its **own** underlying's report; third-party prints (e.g. NVDA 8/26) are ordinary market risk |
| §3.8 | Option exposure counts toward correlation clusters |

At $2,899 competition capital the $580 per-position cap reaches roughly
sub-$140 underlyings at calm IV, ~$95–100 at realistic IV. Capacity
replenishes as positions close and expands as the account grows; scarcity
discipline lives in the $870 open cap and the quality floors.

Two *(strategy rules)* from the comparative research tighten this:

- **Δ≥0.50 for long premium** — the manual's 0.35 floor sits on the boundary
  of the documented Δ 0.05–0.35 lottery-overpricing zone.
- **Unspent is the sleeve's default state, not its fallback.** Retail long
  premium is documented negative-EV at baseline. The evidence-backed uses are
  (a) post-crush continuation calls 1–2 sessions after a qualified beat, when
  IV has reset 30–60% lower, and (b) anti-field long puts. Expect 2–3 shots
  across the window; commission runs 1.3%+ per cap-sized leg.

**All options flat by the 9/4 close** *(strategy rule)*, via escalating limit
reprices ending marketable-at-the-bid. If unfilled at the bell: marketable
limit at the next open, logged as a breach with remedy per §7.3.

**Leveraged ETFs: gated shut by default.** Specific short-horizon dislocation
thesis only, uncorrelated with core+catalyst, ≤20% aggregate, calendar exit
at trading day 4 set at entry. Last routine entry 9/1 *(strategy rule — day-5
must never straddle the dark stretch)*. Also usable in the ⏳ catch-up branch
(§10).

## 7. Order workflow (autonomous once unlocked)

1. §4.9 pre-trade check written to log first — now including **expected stop
   slippage** (realized stop-limit losses skew −12 to −15% on gappy names)
   and the settled-funds gate below
2. Preview; confirm `ACCEPTED`
3. Place; **never resubmit on unknown status** (§4.6) — query until confirmed
4. Write "order placed, verification pending" + order ID to the log
   immediately *(so a successor session knows the suspect order)*
5. Verify fill (§4.8); **place the stop in the same cycle**; verify resting
6. **Manual exits: cancel the resting stop FIRST, then place the exit**
   *(ordering reversed post-review — sell-then-cancel leaves an orphaned GTC
   stop = accidental-short path if the session dies between calls)*. The
   §3.4 gapped-stop market exit likewise cancels the consumed stop-limit
   before selling.
7. Partial fills: amend stop via replace (§4.4). **No entry order may be
   working without a live session watching it** — walking away = cancel
   first *(strategy rule; supersedes "market close" as the §4.2 deadline)*
8. **Settled-funds gate:** until `cashAvailableForTrading` semantics are
   observed with unsettled proceeds present (first real sale), every buy
   gates on `cashAvailableForTrading − unsettledCash` *(wrong only in the
   safe direction)*

**Operational hard gates** *(strategy rules from the automation-failure
research — each traces to a documented incident class)*:

- **Notional sanity check** (Everbright/unit-confusion class): after
  preview, independently recompute qty × price × multiplier and 3-way
  compare — written intent from the §4.9 log, computed notional, previewed
  `orderValue`. Any mismatch beyond rounding aborts the order.
- **Identifier round-trip** (hallucinated-parameter class): every symbol is
  quoted via `get_quotes` immediately before its order; the returned
  description and price must match the written thesis. Option symbols are
  only ever built by `create_option_symbol`, never typed from memory.
- **Order-rate ceilings** (Knight/runaway-loop class): max **2 per symbol
  per session**, max **3 replaces per resting stop per day**. *(The
  5-per-session ceiling was lifted by §9 amendment 2026-08-13.)* Hitting any
  ceiling = stop placing orders, reconcile, write to the log, wait for
  Chris.

  Operational fact (2026-08-13): the Schwab MCP exposes no `replace_order`,
  so a stop amendment is a cancel + immediate re-place. How that cycle is
  counted against the ceilings is defined in **CLAUDE.md §4.10** — one
  place, not restated here.
- **Stale-quote/halt gate**: before any order, and before any stall-rule or
  ratchet decision, the quote's timestamp is checked against
  `get_datetime`; stale quotes (more than a few minutes old in RTH) are
  re-fetched or the action is deferred; tradability is confirmed rather
  than assumed.

Running `scripts/pre-order-check.sh` with the previewed order's numbers
(instrument, qty, price, intent notional, live comp capital, settled cash,
and the flags the instrument requires — the script itself refuses missing,
extra, or wrong-instrument flags, and demands the leveraged aggregate be
asserted explicitly) is now a mandatory step of the notional-sanity gate
above *(strategy rule)* — it runs after preview and before place, spec §12
adoption 1. The script is the calculator: it re-derives the §1.4 floor, the
notional three-way match, the §3.1/§3.2/§3.5 caps (§3.2 counting prior adds
to the same contract via `--existing-option-premium`, and §3.5 also for
options on leveraged ETFs via `--leveraged-underlying` per §3.5's last
bullet), plus the §5 settled-cash check, from the same numbers already in
the §4.9 log entry, so a unit-confusion or cap-arithmetic slip trips
deterministically instead of depending on mental math under time pressure.
The manual remains the authority — this is automation of arithmetic already
required, not a new rule — and the script's NOT-CHECKED block is the
standing reminder of the gates that stay qualitative and stay with the
operator (earnings, corporate actions, §3.8 correlation, halts, §3.6
drawdown, §3.2 option quality floors, order-rate ceilings, quote freshness,
and the header-rule reserve invariant — settled cash includes the $900
reserve, so the §5 check passing does not establish total exposure ≤ 100% of
competition capital). A FAIL from the script aborts the order exactly as a
§4.10 mismatch does: stop, reconcile, do not resubmit blind.

## 8. Monitoring — the 5-minute loop

Risk watchdog. Honest coverage: **5–15% of the trading day, ≈0% at open and
close** — the loop covers live sessions; resting GTC stops cover the rest;
Chris's 9:30 ET check-in covers the gap that matters most. Watches:

| Watch | Trigger | Action |
|---|---|---|
| **Naked position** | position without resting stop | Place stop immediately; if it won't take, close (§4.3) |
| Stop fill | position gone / stop consumed | Log exit; check orphaned remainder (§4.7); **redeploy cap = actual proceeds (§3 invariant)** |
| Partial fill | filled qty ≠ stop qty | Replace stop (§4.4) |
| Drawdown | comp value vs **0.88×HWM / 0.80×HWM** (recorded HWM — resolved once per session from the latest status file; ratchets only at the session-close §7.2 write, never intraday) | Caution/Halt per §3 |
| Reserve | total cash < $900, computed per tick.md watch 2 (canonical — a conservative min() over both candidate totals until §7.8's field-semantics observation) | Invariant breach — halt buys, investigate |
| Clocks | option DTE, leveraged day count, **9/4** flats, 9/10 lockout | Escalating from 2 days out |
| Correlation | weekly: 60-day corr of held names | >0.7 cluster → adds blocked |
| Restriction | `isClosingOnlyRestricted` true | §5 protocol: read-only, notify |

**Research loop** *(strategy rule, added 2026-08-14 — design:
`2026-08-14-research-loop-design.md`)*: a second pass chained after the
tick (`/research`, self-gated to ~45 min) maintains
`research/candidates.md` — a WATCH/HOT tiered list of qualified candidates
with tombstones — via the read-only `research-scout` subagent. Advisory
plus rate-limited pings (max 2/day, parent-gated on capacity, drawdown,
and calendar guards). The file is never a source for order parameters;
entries re-verify live under §4.9/§4.10. Session open and post-exit
redeployment read it as the starting candidate set. *(Amended 2026-08-14,
spec §8 — Chris: "Do both")*: option candidates additionally clear a
**ladder/IV assessment** before HOT (chain-wide spreads, OI distribution,
IV vs ~20-day realized — elevated IV defaults to reject for a
premium-buying book), and the post-close pass runs a **daily OI-delta
snapshot** (`research/oi/`, ≤ 6 underlyings) whose anomalies feed WATCH
only — an idea source, never a signal.

## 9. Session protocol

**Open:** §4.5 broker-first reconciliation → §3.6 check (ratcheted
thresholds; resolve `recorded_hwm` per tick.md §B5 **including its orphaned-
ledger recovery rule** — at reconciliation time, before any order, not only
at the first tick) → reserve invariant → diff vs trade log (unexplained
differences investigated before any order) → loop → planned actions.

- **Deep-research deadman (design rev2 §8.5):** check that yesterday's
  `research/preopen/` brief and `research/screen/` jsonl exist (mtime).
  If either is missing, say so in the session-open summary as a status
  line — not ALERT.md. A dead research loop gets noticed here, not weeks
  later. (Quiet, not loud — but never invisible.)
- **Cron re-creation:** session open also re-creates the two
  `/deep-research` cron entries if absent (`.claude/commands/deep-research.md`
  §Dispatch) — the deadman above only detects a dead loop a day late;
  re-creating the crons here prevents it from going dead in the first place.

**Weekly, first session of the week** *(strategy rule)*: compute the 60-day
daily-return correlation of held names at the open, before the loop starts —
it needs price-history pulls the tick call budget forbids, so it lives here,
not in the tick. **Record the result (pairs, values, computed date) in
today's status file, creating the file at open** — the tick's watch 8
confirms against that written record, never memory. >0.7 cluster → adds
blocked (§3.8).
**Close-write format** *(strategy rule)*: every session-close status file
ends with a **"State recorded — current"** block (account value, competition
capital, HWM, settled/unsettled cash) — tick.md §B5 resolves `recorded_hwm`
from exactly that heading, so a close that names it differently orphans the
next session's HWM lookup.
**Data corpus** *(strategy rule, added 2026-08-13 — Chris: "We might even
want to write out all our acquired data every day so we can do a review
and reinforcement")*: session state that isn't in the trade log or tick
ledger evaporates with the context — quotes at decision time, approval
latencies, fill details, and above all the *reasoning*. Capture it as it
happens via `scripts/data-append.sh KIND DATE 'JSON'` into
`status/data/DATE-KIND.jsonl`:
- **orders** — every preview/place/cancel/fill-verify, with request and
  confirm timestamps (Discord approval latency is the difference);
- **quotes** — every quote read at a §4.9/§4.10 gate and what it fed;
- **decisions** — everything decided *with the reasoning*: entries taken
  and skipped, thesis re-verification at the live price, cadence choices.
  This is the reinforcement half — outcomes can be scored against stated
  reasons at the nightly review;
- **events** — approvals, rejects, trips, alerts, restarts, anomalies.
- **counterfactuals** *(added 2026-08-13 — Chris: "if the rules had been
  different, I would have made x decision differently. We want to identify
  when our guardrails are hurting more than helping")* — **every time a
  rule binds**: blocks an action, resizes it, delays it, or forces one.
  Record which rule, what was wanted, what was done instead, and a
  reference price so the road not taken can be marked to market later.
  This structures §7.2's "any rule that bound a decision" into scoreable
  data.

  **Scoring discipline** *(strategy rule — the method is the safeguard)*:
  1. **Symmetric, always.** A blocked trade that would have lost is a
     guardrail *win* and gets recorded with the same diligence as a
     blocked winner. Score every counterfactual at review time and again
     at `score_at` (default +5 sessions) — never only the ones that sting.
  2. **Tail-risk rules are scored on avoided ruin, not daily P/L.**
     Settlement discipline (§5), expiration handling (§3.3), the
     naked-position machinery (§3.4/§4.3), position caps at their
     extremes — these pay off once, catastrophically rarely, and a 21-day
     sample will make them look like pure cost. The ledger notes what the
     rule is insurance against; "it never fired" is not evidence it hurts.
  3. **This ledger feeds §9 conversations in calm conditions only.** It
     exists to tune rules between sessions with Chris — never to justify
     bending one mid-position. §0's warning is the design premise: the
     pressure to loosen peaks exactly when loosening is most wrong, and a
     tally of "what the rules cost us" is that pressure in spreadsheet
     form. Amendments still go through §9, flat and calm.
Clean ticks are exempt (the tick ledger is that record). Commit the day's
corpus with the session close; the close-of-market review reads it.
**Close = session end, whenever that is:** no working entry orders survive
the session; stops match filled quantities; status file; commit.
**Token:** re-auth 8/19, 8/26, 9/2, **9/8 pre-market**. **Token-death rule**
*(strategy rule)*: if Chris knows he cannot re-auth before ≥2 consecutive
dark days, positions are flattened the prior session or the accepted gap risk
is logged in writing beforehand.
**Long-session re-grounding** *(strategy rule)*: a session older than ~3
hours re-reads CLAUDE.md and re-runs §4.5 reconciliation before its next
order — late-session orders must not rest on early-session readings.
**Alert protocol** *(strategy rule)*: alerts requiring Chris are written to
`ALERT.md` at the repo root and committed, in addition to the conversation.
An unacknowledged alert at the next session open puts the account in
closing-only posture until Chris responds. Chris's 9:30 ET check-ins are
noted in the daily status file so the human heartbeat is verifiable.

## 10. Endgame and continuation

| Date | Action |
|---|---|
| 9/1 | Last routine leveraged-ETF entry |
| **9/4 close** | **All options flat. All discretionary clocks satisfied. Last routine new entry.** Nothing mandatory sits beyond the dark stretch |
| 9/5–9/7 | Dark (weekend + Labor Day). Book carries only stopped equity positions |
| 9/8 pre-market | Re-auth (token → past 9/14). Full reconciliation |
| 9/8–9/9 | **Catch-up branch: REJECTED by Chris, 2026-08-13, in calm conditions.** No late-variance rotation under any circumstances, regardless of standing. If behind on 9/8, the book holds posture and finishes where it finishes. This rejection is final for the window and is not revisited under pressure — §9.3 applies |
| 9/10–9/14 | Lockout (**from** 9/10): no new positions; stops and §3.5 forced closes still run |
| 9/14 16:00 | Final value; **score = value − $900**; final commit |
| After | Nothing forced. Positions worth holding stay held. §9 conversation revisits competition-scoped rules in calm conditions |

## 11. Open items

1. First-sale observation of `cashAvailableForTrading` semantics (§7.8).
   Until then every buy gates on `cashAvailableForTrading − unsettledCash`
   (§7.8) — wrong only in the safe direction.

*(Closed: the four post-review rulings, 2026-08-13 — ratchet adopted,
own-earnings §3.7 reading, catch-up branch rejected, §3 body text propagated.
Execution unlock and the place/cancel round-trip drill, 2026-08-14.)*

## 12. Success criteria

- Zero manual violations, GFVs, §3.3 events, orphaned stops, or naked
  intervals longer than one session.
- Every trade: §4.9 entry logged before the order, §7.2 status after.
- §3.6 never fires because sizing worked — or fires and is obeyed exactly.
- The book was positioned for its actual win condition (field-down
  scenarios) whenever a compliant instrument existed for it.
- Catalyst sleeve judged as **momentum continuation with an earnings-event
  entry filter** — not as "drift capture."
- **The field is logged**: competitors' visible outcomes recorded at each
  checkpoint, building our own variance-vs-skill evidence for the moment a
  competitor's lottery ticket hits and amendment pressure arrives.
- If the month is lost, it is lost to the box's stated cost (no NVDA-scale
  variance available), not to a rule breach, a missed deadline, or an
  operational failure. Losses reported per §7.3, same detail as gains.

---

*Revision history for this document — and for `CLAUDE.md` — is in
`CHANGELOG.md` at the repository root.*
