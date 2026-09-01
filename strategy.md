# Trading Strategy Playbook

**The playbook.** How the account is actually traded, inside the box that
`CLAUDE.md` defines. Required reading at every session open.

**Governed by `CLAUDE.md`. Where this document and the manual disagree, the
manual wins** — and the disagreement is a defect to fix, not a choice to make.
Rules here stricter than the manual are marked *(strategy rule)*:
discretionary, changeable without a §9 amendment.

Origins: a four-agent adversarial review and a five-agent comparative
research pass, both in `docs/superpowers/specs/`. Revision history in
`CHANGELOG.md`. Rule parameters live in `rules.yml` — this document cites
manual sections rather than repeating their numbers.

---

## 1. Goal — stated honestly

**Rewritten 2026-08-31.** Chris withdrew from the competition on 2026-08-29
and the manual's §8 was deleted the same week per §9. This section used to
state a winner-take-all objective, a 2026-09-14 score, and a P(first)
estimate. All three are gone. What survives is the part that was never about
the contest:

- **The objective is the sequence, not a date.** Chris's 2026-08-13 horizon
  correction — "9/14 is a checkpoint, not a terminus" — turned out to be the
  whole story rather than a caveat on it. There is now no checkpoint at all:
  the account compounds or it does not, and every rule is judged on whether it
  survives being run for years rather than for a month.
- **The box is still the point.** `CLAUDE.md` §1's prohibitions were adopted
  because they make near-zero the probability of destroying real money. That
  argument never referenced the competition and does not weaken without it.
- Marks are honest by construction: §1.4/§2 liquidity floors mean quoted
  prices are realizable prices.
- **What replaced the old edge thesis.** v1 selected on generic factor
  screening at a three-week horizon and was, separately, structurally
  incapable of opening a position. v2's edge is Chris's domain knowledge in
  three sectors applied to dispersed public information before it is
  aggregated into price — see
  `docs/superpowers/specs/2026-08-30-information-edge-scout-design.md`.
  **No validated hit rate exists yet**, which is exactly why §3.2's
  single-thesis premium cap was cut to 10%.

## 2. Operating constraints (verified 2026-08-13)

| Constraint | Consequence |
|---|---|
| Execution autonomous. MCP gate flipped 2026-08-14; place/cancel round-trip drill passed the same session. `replace_order` is absent from the tool surface — a stop amendment is cancel + re-place, counted per CLAUDE.md §4.10 | I place orders under §6 |
| Stop rejected until entry fills (verified live) | Fill→stop window real; naked-position watch is the loop's top alert |
| T+1 cash account, zero GFV tolerance | $900 reserve as float; invariant in §3; field semantics gated (§7.6) |
| Token expires every 7 days | Re-auth roughly weekly, pre-market. There is no longer a terminal date the token has to outlive; it is a standing operational chore |
| Loop coverage honestly ≈ 5–15% of the trading day, ≈0% at open/close | Operative deadline everywhere = **session end**, never "market close"; Chris does a 5-min 9:30 ET check any day a position is open |
| §3.2 per-position option cap: 10% of account value | Cut from 20% on 2026-08-31 per §9. At present capital this reaches mid-priced underlyings at calm IV and appreciably less at realistic IV — which is the intended pressure, not a defect. Re-derive at order time; never carry a spot ceiling forward |
| Earnings are a **timing input**, not a barrier (§3.7, amended 2026-08-31) | The cohort builder schedules research off the calendar rather than avoiding it. Names reporting in 21–42 days are the working set (`rules.yml` `scout_entry_window_*`) |

## 3. Capital architecture

**Every ceiling is a percentage of account value** — the whole broker balance
— since the 2026-08-31 amendment. *(It was "competition capital" = account
value − $900.00, a second capital number that existed to make scoring and risk
agree. With no score, it was one more thing to get wrong.)*

**The reserve invariant** *(strategy rule)*: **total cash (settled +
unsettled) ≥ $900.00 at all times.** This is now a pure settlement buffer and
is stated directly as a cash floor rather than derived from a capital
definition. A bridged buy is capped at min(planned size, incoming unsettled
proceeds); same-day proceeds qualify. This forbids the leak where a
stopped-out loss plus full-size redeployment quietly eats the reserve:
**redeploys are capped at actual proceeds, not at original position size.**

Note the consequence, stated plainly: because §3 percentages now take account
value rather than account value − $900, **every equity ceiling is nominally
larger in dollars than it was**, while the reserve invariant above is what
actually stops the book from consuming the buffer. The options sleeve moved
the other way and moved further — 20% → 10% per thesis.

Ceilings are percentages of account value. Dollar equivalents are
deliberately not written here — they drift with capital and go stale silently
(manual header, *Percentages are canonical*). `scripts/pre-order-check.sh`
computes them from the live figure at order time.

| Sleeve | Ceiling | Positions |
|---|---|---|
| Core | **50%**<!--rule:strategy_sleeve_core_pct--> | No name count *(strategy rule)*; per-position §3.1/§3.8 bind |
| Catalyst | **30%**<!--rule:strategy_sleeve_catalyst_pct--> | 1, occasionally 2 |
| Options | 30% open / 10% per position (§3.2) | No count limit |
| Leveraged ETF | **20%**<!--rule:strategy_sleeve_leveraged_pct--> aggregate, within the rows above | Gated (§6) |
| **Max deployed** | **100%**<!--rule:strategy_max_deployed_pct--> | Sleeves sum to 110% — they are individual ceilings; the 100% total-deployment line and the reserve invariant bind first |

- **Sleeve compliance is checked at order time only**, against account value
  at current marks. Mark drift never forces a sale and never frees
  option capacity. "Open premium" (§3.2) = premium **paid** on open
  positions; marks irrelevant.
- **§3.6 threshold ratchets:** Halt is the manual's multiple of the HWM of
  **account value**. It is
  the only drawdown level — nothing happens above it (§3.6). HWM resolution —
  source, once-per-session caching, and the no-intraday-ratchet rule — is
  defined in tick.md §B5, one place.
- **§3.8 method** *(strategy rule)*: evaluated **at entry** — sector/theme
  classification plus trailing 60-day daily-return correlation (>0.7 =
  correlated). Mid-hold correlation convergence in a selloff is not a
  violation but **blocks all adds** to the correlated cluster. Weekly
  correlation check in the monitoring table.
- Deployment pace: core days 1–2, catalyst on qualified setups only. Partial
  deployment is legitimate (§3.9).

## 4. Core sleeve — selection

- Universe: large/mid caps and sector ETFs passing §1.4/§2 floors. **The
  "no earnings inside the holding period" screen is removed** (§3.7 amended
  2026-08-31); note each name's report date at entry as context for the exit
  plan, and let §3.4's stop and §3.1's size carry the event risk.
- Tilt: 3–6 month relative strength, uptrend, proximity to 52-week high
  (the tilt with live large-cap evidence at this horizon).
- Volatility ceiling *(strategy rule)*: reject if daily ATR exceeds
  **6%**<!--rule:strategy_max_daily_atr_pct--> — the point at which §3.4's
  ATR-scaled stop would hit its cap and stop being proportional to the name.
  **For catalyst entries, ATR is measured on the post-gap series** — pre-gap
  ATR systematically passes names whose post-print volatility makes a stop a
  coin flip *(comparative research #2)*. *(Was "reject if 10% ÷ ATR% < 3",
  i.e. ATR% > 3.33 — a threshold that existed only to accommodate a fixed 10%
  stop, and that rejected quality names for volatility the stop can now
  absorb.)*
- **NVDA-week guard (8/25–8/27)** *(strategy rule)*: through the print, no
  adds and no new positions **in names correlated with the AI/semis complex
  or with the broad index** (§3.8 method, >0.7). Uncorrelated single names —
  healthcare, staples, energy, rate-sensitive financials — remain tradeable
  on their own merits. De-grossing and anti-field expressions remain
  permitted. Breadth is narrow and margin debt is at a
  2000/2007/2021-signature record, so our win path is surviving the field's
  event rather than joining it — but that argues against *correlated*
  exposure, not against trading at all. *(Was 8/24–8/28 and a blanket freeze:
  five sessions closed to every name regardless of
  whether it had any connection to the event.)* Expect a muted
  tape to produce few qualified catalyst setups generally (Q4-25: under half
  of beats saw a positive next-day move); **zero qualified setups is a
  legitimate outcome.**

## 5. Catalyst sleeve — post-earnings momentum continuation

Relabeled honestly: academic PEAD is dead in our forced (liquid, large-cap)
universe. What the qualification rules actually select — a
high-turnover large cap gapping to near its 52-week high on a clean beat —
carries a **short-term momentum continuation** premium with current,
replicated large-cap evidence. Same trades; correct theory; success judged
accordingly (§12).

Entry remains **after** the print for this sleeve — *(strategy rule as of
2026-08-31, no longer a §3.7 requirement)*. §3.7's earnings bar is gone, so
holding through a print is now permitted by the manual; this sleeve
nonetheless keeps the post-print entry because its thesis **is** the
post-print drift and the IV/gap math is what makes it work. The
scout/catalyst channel is where through-the-print exposure now lives, and it
expresses that view in long options (§6), not in shares.

**Qualification (all required):** reported within last 1–3 sessions; clean
beat, ideally raised guidance; **gapped up and held** (closed report day in
top half of range); §1.4/§2 floors; sector distinct from both core names
(§3.8 — guaranteed possible by §4's carve-out); next report ~3 months out.
Price is bounded by **granularity, not a fixed band** *(strategy rule)*: the
catalyst sleeve must buy at least
**3**<!--rule:strategy_catalyst_min_whole_shares--> whole shares, so the
ceiling is `sleeve ÷ 3` and rises with capital. The floor is §1.4's. *(Was a
fixed ~$20–60 band, set when the sleeve was $270 and never rescaled; at
present sleeve size it excluded most of the S&P by price for a granularity
problem that no longer exists.)* *(Amended 2026-08-20 — was 6 shares, a
~$145 ceiling that blocked TGT and HD while both stayed core-eligible. That
made the ceiling decide **which sleeve a thesis belonged to**, which is not
what a granularity rule is for; the alternative on the table was routing
such names to core, which would have given a momentum trade core's slower
exits. Chris chose the root cause. **Accepted cost: a 3-share position
scales coarsely** — partial exits move in thirds — so on 3-share positions
the ratchet does the work that scaling out would otherwise do.)*

**Entry:** sessions 1–3 post-report, limit at/inside ask, day-only, and only
in the first half of a session Chris intends to keep open *(strategy rule)*.

**Exits — first trigger wins (no time exit; it clipped the only payoff):**
- §3.4 stop-limit, GTC, at the manual's ATR-scaled trigger and its limit
  offset below the **share-weighted average entry**; re-computed via single
  replace on each add
- Stall rule: two consecutive closes below blended entry, evaluated from
  session 4 onward (entry day = session 1) → close next session
- Stop ratchet — **active** (§3.4: the trigger is a floor, may be raised,
  never lowered): **+8%**<!--rule:strategy_ratchet_breakeven_at_gain_pct--> → breakeven, **+15%**<!--rule:strategy_ratchet_entry_plus8_at_gain_pct--> → entry+8%, limit
  always 5% below trigger, single-message replace only, §4.6 applies to
  replaces.

## 6. Options and leveraged ETFs

**Options are a standalone sleeve** (Chris, 2026-08-13: "You should be free
to plan options at any point"). Any directional thesis — long calls or long
puts, catalyst-linked, anti-field, or independent — may be expressed in
options, provided the position carries its own written §4.9 rationale before
the order.

What binds, from the manual:

| Rule | Constraint |
|---|---|
| §3.2 quality floors | DTE, delta, open interest and spread floors exactly as manual §3.2 states them (`rules.yml`) |
| §3.2 caps | Per-position and total-open premium caps per manual §3.2. No position-count limit, no cumulative budget; premium still logged per §7.2 |
| §3.3 | Each open position carries its own expiration clock into the monitoring table |
| §3.7 | No option held through its **own** underlying's report; third-party prints (e.g. NVDA 8/26) are ordinary market risk |
| §3.8 | Option exposure counts toward correlation clusters |

The affordable spot ceiling follows from the per-position cap and the
contract's premium-to-spot ratio, so it moves with capital and with IV —
re-derive it at order time from the live chain rather than carrying a number
forward. Capacity replenishes as positions close and expands as the account
grows; scarcity discipline lives in the 30% open cap and the quality floors.

Two *(strategy rules)* from the comparative research tighten this:

- **Δ ≥ 0.50**<!--rule:strategy_option_min_delta--> **for long premium** — one
  step inside the manual's §3.2 band floor of 0.45, leaving the ceiling of
  0.75 as the manual states it. *(Raised from 0.40 on 2026-08-31, tracking the
  manual's move from a 0.35 floor to a 0.45–0.75 band.)* The reasoning changed
  along with the number: the old buffer existed to clear the Δ 0.05–0.35
  lottery-overpricing zone. The band exists for a different hazard — buying
  options into a scheduled print means buying into a known post-event
  volatility collapse, and that crush destroys **extrinsic** value, so a
  contract that is mostly intrinsic survives it. The earlier objection to 0.50
  (it forced deep, expensive contracts, few of them) is now the intended
  trade-off rather than a cost: fewer, higher-conviction, crush-resistant
  positions is the design.
- **Expiry sits 14–28 days past the print** and never on the front weekly,
  where event vol is most concentrated (`rules.yml`
  `strategy.option_expiry_*_days_past_earnings`). The point is that a correct
  thesis is never *forced* to sell into the crush.
- **Entry sits 21–42 days before the print** (`rules.yml`
  `strategy.scout_entry_window_*`), which is deliberately the same interval as
  the research window: IV ramps in the final ~2 weeks, so entering early buys
  vol before it is bid up.
- **Unspent is the sleeve's default state, not its fallback.** Retail long
  premium is documented negative-EV at baseline, and this structure minimises
  what we pay for the earnings volatility premium without defeating it — the
  premium is real and it is against us. The thesis has to be right often
  enough to clear it. Commission runs 1.3%+ per cap-sized leg.

**Leveraged ETFs: gated shut by default.** Specific short-horizon dislocation
thesis only, uncorrelated with core+catalyst, ≤20% aggregate, calendar exit
at **trading day 4** set at entry *(strategy rule — one session inside the
manual's §3.5 day-5 limit, so a missed session cannot breach it)*.

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
- **Order-rate ceilings** (Knight/runaway-loop class): per-symbol and
  per-stop ceilings exactly as manual §4.10 states them (`rules.yml`). *(The
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
and the reserve invariant — settled cash includes the $900
reserve, so the §5 check passing does not establish that total cash stays
≥ $900.00). A FAIL from the script aborts the order exactly as a
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
| Drawdown | account value vs the §3.6 Halt multiple of HWM (recorded HWM — resolved once per session from the latest status file; ratchets only at the session-close §7.2 write, never intraday) | Halt per §3.6 — the only level |
| Reserve | total cash < $900, computed per tick.md watch 2 (canonical — a conservative min() over both candidate totals until §7.8's field-semantics observation) | Invariant breach — halt buys, investigate |
| Clocks | option DTE (§3.3 close at 5), leveraged day count (§3.5) | Escalating from 2 days out |
| Correlation | weekly: 60-day corr of held names | >0.7 cluster → adds blocked |
| Restriction | `isClosingOnlyRestricted` true | §5 protocol: read-only, notify |

**Research loop** *(strategy rule, added 2026-08-14 — design:
`docs/superpowers/specs/2026-08-14-research-loop-design.md`)*: a second pass chained after the
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

**Open:** §4.5 broker-first reconciliation → `scripts/check-consistency.sh`
(rule drift is a defect, fix before trading) → §3.6 check (ratcheted
thresholds; resolve `recorded_hwm` per tick.md §B5 **including its orphaned-
ledger recovery rule** — at reconciliation time, before any order, not only
at the first tick) → reserve invariant → diff vs trade log (unexplained
differences investigated before any order) → loop → planned actions.

- **Deep-research deadman (design rev2 §8.5):** check that yesterday's
  `research/preopen/` brief and `research/screen/` jsonl exist (mtime).
  If either is missing, say so in the session-open summary as a status
  line — not ALERT.md. A dead research loop gets noticed here, not weeks
  later. (Quiet, not loud — but never invisible.)
- **Pre-open catch-up** *(strategy rule, added 2026-08-19)*: if today's
  `research/preopen/DATE.md` does not exist **and it is before 12:00 ET**,
  run `/deep-research preopen` here, once — after the §3.6 check, before the
  monitoring loop starts. This began as cover for session-scoped crons that
  died with the session — which cost four consecutive briefs, 8/15 through
  8/19, before it was read as a pattern rather than four incidents. The
  schedule now lives on the server and no longer dies, but the catch-up
  **stays**: it covers a container that was down, a job that fired outside its
  08:00-09:15 ET window, and a Schwab token that lapsed overnight.
  **The scheduled job is the fast path; this is the guarantee.** If the file already exists, no-op — never
  double-write a brief the cron already produced. **After 12:00 ET, skip it
  and say so in the session-open summary:** by midday the live `/research`
  loop has been reading the tape on RTH data, and a "pre-open" brief written
  then is a backdated file describing a session already half over. The
  catch-up inherits every §P prohibition unchanged — file-only, no pings, no
  §E, no HOT promotions — and stamps its real run time in the body
  (`.claude/commands/deep-research.md` §P).
- **Weekly universe deadman:** check the `Assembled:` timestamp inside
  `research/universe.md` — the line `/weekly-universe` writes into the body.
  If it is older than 8 days, the weekly sweep has not run: say so in the
  session-open summary as a status line (not `ALERT.md`) and **run
  `/weekly-universe` manually** — the scheduled Saturday 07:40 ET job
  (`docker/crontab`) is the cadence now, so a stale stamp means that job did
  not fire and the manual run is the recovery, not the routine. Read the
  in-file stamp, **not the file mtime**: `research/universe.md` is
  git-tracked, and a checkout, rebase, or stash touches its mtime without
  regenerating a thing — an mtime deadman would report a fresh universe on a
  sweep that never ran. A stale working universe silently narrows discovery,
  which is exactly the failure the weekly tier was built to remove.
- **Scheduled jobs run on the server, not in the session.** There is nothing
  to re-create at open. `docker/crontab` fires preopen, postclose and the
  weekly sweep on the always-on box (`.claude/commands/deep-research.md`
  §Dispatch), and `scripts/job-deadman.sh` reports a miss to Discord the same
  morning. This bullet used to say "session open also re-creates the two
  `/deep-research` cron entries if absent" — a mitigation for session-scoped
  crons that could only ever prevent tomorrow's miss, on a laptop that was
  asleep at the fire time anyway.

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

**The `Competition capital:` line is a retained legacy field name**, not a
live rule anchor. Since 2026-08-31 every §3 cap is taken against **account
value**; the line is still written (account value − $900.00) so that the
existing `status/` history stays parseable and `scripts/status-write.sh` and
its three test suites keep agreeing on the format. Read `Account value:` for
sizing and `High-water mark:` for §3.6. Renaming the field is a data-format
migration, deliberately not bundled into a rules amendment.
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
**Token:** re-auth roughly weekly, pre-market, on a standing rota rather than
against a fixed end date. **Token-death rule**
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

## 10. Continuation

**The endgame calendar was deleted 2026-08-31**, alongside the manual's §8.
It scheduled a last leveraged entry (9/1), an all-options-flat date (9/4), a
lockout (9/10) and a final mark (9/14) — every one of them a deadline serving
a contest that Chris left on 2026-08-29. `rules.yml` no longer carries
`all_options_flat_by` or `last_leveraged_entry`, and
`scripts/check-consistency.sh` fails the build if either returns.

There is no terminal date, so nothing is forced by the calendar. The clocks
that remain are risk clocks and are unchanged:

| Clock | Rule | Why it survives |
|---|---|---|
| Close every long option at **5 DTE** | manual §3.3 | OCC auto-exercise on a cash account, which §3.7's amendment makes *more* likely by design — we now hold options through prints |
| Leveraged/inverse ETF out by **session 4** | strategy §6, inside manual §3.5's 5 | Daily-reset decay; one session of slack so a missed session cannot breach the manual |
| Re-auth roughly weekly | operational | A lapsed token costs all read access |

**One deliberate carry-over.** Chris's 2026-08-13 rejection of the
"catch-up branch" — no late-variance rotation to make up ground, ever — was
argued as final for the competition window. The window is gone; **the ruling
is kept**, because its reasoning ("if behind, the book holds posture") was
about behaviour under pressure, not about a deadline, and §9.3 forbids
loosening a rule at the moment it would help. Reopening it is a §9
conversation Chris can start in calm conditions.

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
