# Trading Strategy Playbook

**The playbook.** How the account is traded, inside the box that `CLAUDE.md`
defines. Required reading at every session open.

**Governed by `CLAUDE.md`. Where this document and the manual disagree, the
manual wins** — and the disagreement is a defect to fix, not a choice to make.
Rules here are *strategy rules*: stricter than or inside the manual,
discretionary, and changeable without a §9 amendment. Their numbers live in
`rules.yml`; the markers below bind each stated number to it.

**Rewritten 2026-09-27** for the trading desk
(`docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md`). What the
previous playbook said, and why it went, is in `CHANGELOG.md` and §12.

---

## 1. Goal

- **Test Claude as a trader.** Chris, 2026-09-27. Claude's judgement picks the
  trades; the question is whether it beats holding SPY.
- **The verdict is pre-registered** (§8) and was fixed before any result
  existed.
- **The box is still the point.** `CLAUDE.md` §1's prohibitions make near-zero
  the probability of destroying real money, and they bind the desk exactly as
  they bound everything before it.
- **Honest expectation:** the most likely result is no reliable edge. The desk
  exists to find that out cheaply, on the record, and — if there is an edge —
  to show where it is.

## 2. Operating constraints

| Constraint | Consequence |
|---|---|
| Cash account, T+1, zero GFV tolerance | Settled cash gates every buy; the reserve invariant (§3) keeps a buffer |
| Schwab token dies after 7 days | Re-auth weekly from the phone; a dead token makes the engine BLIND and the desk skips its day |
| The server shares Chris's Claude subscription | Heavy Claude work happens outside market hours; only the PM runs in them. Build sessions stay outside 09:30–16:30 ET |
| Every entry passes a Discord veto window | Chris's availability does not cap the trade count; his ❌ still stops any entry |
| **Paper phase** | Until revised Plan 1 lands and the §9 amendment in spec §12.3 is committed, funded calls fill a paper book and nothing reaches Schwab (§10) |

## 3. Capital, sizing and the reserve

**The reserve invariant**: **total cash (settled + unsettled) ≥ $900.00 at
all times.** A buy that would breach it is refused in code.

Every percentage is of **account value** — during the paper phase, of the
paper book's equity. Conviction sets the size:

| Conviction | Shares/ETF | Option premium |
|---|---|---|
| 1–2 | never funded — scored only | never funded |
| 3 (the funding floor, **3**<!--rule:strategy_min_fundable_conviction-->) | **10%**<!--rule:strategy_size_shares_pct_conviction_3--> | **5%**<!--rule:strategy_size_option_premium_pct_conviction_3--> |
| 4 | **15%**<!--rule:strategy_size_shares_pct_conviction_4--> | **7.5%**<!--rule:strategy_size_option_premium_pct_conviction_4--> |
| 5 | **20%**<!--rule:strategy_size_shares_pct_conviction_5--> | **10%**<!--rule:strategy_size_option_premium_pct_conviction_5--> (the §3.2 cap) |

- At most **8**<!--rule:strategy_max_funded_positions--> funded positions,
  legacy holdings and pending proposals included. **In the paper phase** the
  count is paper positions plus pending proposals — legacy holdings sit in
  the paper book as cash (§10) and count for nothing until the book is real,
  at which point they count as positions.
- Share funding requires daily ATR ≤ **6%**<!--rule:strategy_max_daily_atr_pct-->;
  a noisier name can be expressed only as an option.
- `CLAUDE.md` §3.1 (single position, counting what is already held or
  pending in the same name), §3.2 (option premium, per position and open)
  and §3.8 (the correlated cluster) are checked in code at proposal time —
  with every pending proposal counted at its worst case (maximum entry price
  × quantity), so the calls of one PM run cannot jointly breach what each
  clears alone — and again at the paper fill, at the price actually paid. **§3.8 is a cap, never a same-sector ban**: correlated means the same
  sector benchmark (SPY clusters nothing) or a 60-day return correlation over
  the manual's threshold.

## 4. The desk

| ET | Who | What |
|---|---|---|
| 16:10 | engine | Daily bars for the liquid universe's head by dollar volume, the ETF list, context rows and every carried symbol; then scoring |
| 16:30 → ~18:10 | analysts, in a chain | technical → earnings → news/catalyst → macro/ETF (Sonnet). Each reads its engine-computed briefing and files pitches |
| 08:00 | news, earnings | Pre-open mode: overnight news and pre-market reports; new pitches and withdrawals |
| **09:50** | **PM** (Opus) | Book, pitches, calls, funding. The one heavy Claude run in market hours |
| 12:30 | PM, midday mode | Held positions and today's news only; no new calls |
| every 5 min 09:55–15:55 | engine (`desk_watch`) | Paper entries at the veto deadline; paper exits |
| every 15 min | engine (`tick`) | The real book's watches, unchanged |
| Sat 08:30 | engine | The scorecard, posted |

Analysts file **pitches**. The PM makes **calls**. The engine stamps every
reference price from broker reads — **no price the model types is ever a
reference** — sizes, fills, exits and scores.

## 5. Pitches

- At most **5**<!--rule:strategy_desk_max_pitches_per_analyst_run--> per
  analyst per evening run, and at most
  **2**<!--rule:strategy_desk_preopen_max_new_pitches--> new in the pre-open
  run. Filed only outside the regular session.
- Horizon **2**<!--rule:strategy_pitch_horizon_min_days-->–**20**<!--rule:strategy_pitch_horizon_max_days-->
  trading days; target and invalidation on the correct sides of the price and
  within **30%**<!--rule:strategy_pitch_level_max_distance_pct--> of it.
- One open pitch per analyst per symbol and direction.
- The benchmark is SPY or one of the 11 sector SPDRs; ETF pitches use SPY.
- A pitch's reference is its session's opening print. It may be withdrawn
  only before that open; after it, it can only resolve.

## 6. Calls and options

- At most **5**<!--rule:strategy_desk_max_new_calls_per_day--> new calls a
  day, adopting a pitch or originating one. Extensions and legacy calls do
  not count. One open call per symbol and direction.
- A call's reference is the live quote when it is made; SPY and the benchmark
  are stamped with it. A quote older than five minutes is refused (§4.10).
- **Up calls** are funded with shares, an ETF or a long call; **down calls**
  with a long put. No leveraged or inverse ETFs.
- **Option contracts** come only from the engine's candidate list: every
  `CLAUDE.md` §3.2 floor (days to expiry, delta band, open interest, spread,
  premium cap) must hold, with expiry long enough to outlast the horizon plus
  the §3.3 5-DTE close plus a week, and at most
  **60**<!--rule:strategy_option_max_dte--> days out. Candidates rank toward
  delta **0.60**<!--rule:strategy_option_target_delta-->; the strategy's
  delta floor equals the manual's, **0.45**<!--rule:strategy_option_min_delta-->.

## 7. Entries and exits

**Entries.** A funded call becomes a Discord proposal. It executes
**10**<!--rule:strategy_veto_window_minutes--> minutes after posting (never
before 10:00 ET) unless Chris reacts ❌; ✅ executes at once. The PM sets a
maximum entry price no more than **5%**<!--rule:strategy_max_entry_chase_pct-->
above the last; if the ask has run past it at execution, the entry is skipped
and the call is still scored. Proposals go out between 10:00 and 15:00;
unfilled entries end at 15:55 (§4.2).

**Exits without a veto are engine code carrying out a standing rule, never a
fresh discretionary call** — a protective stop, the §3.3 5-DTE close, a §3.5
forced close, or a call resolving at its recorded target, invalidation or
horizon:
- Shares carry a stop whose trigger is the **higher** of the §3.4 formula and
  the call's invalidation (a trigger may be raised, never lowered).
- Target reached → sell at the bid. Options exit on their underlying crossing
  the invalidation or reaching the target, and at 5 DTE (§3.3).
- A funded call that resolved at the previous close exits after 10:00 unless
  the PM extends it — once.

**A discretionary PM exit or tighten (09:50, 12:30) is not veto-free.** It is
paper-only today; once real orders exist it goes through the same Discord
approval gate as any other order (`CLAUDE.md` §0).

## 8. Scoring and the checkpoint — pre-registered

Every pitch and every call resolves once, from daily bars: target touched,
invalidation touched (both on one day counts as invalidation), or the
horizon's close. Return is signed by direction. **Excess** is measured two
ways, and they are deliberately not symmetric:

- **against SPY**, the call's signed return minus SPY's *plain* (unsigned)
  return over the same window — the opportunity cost of the call against
  simply holding SPY, whichever way the call pointed;
- **against the benchmark**, the call's signed return minus the benchmark's
  return *signed by the call's direction* — a relative call: a down call
  wins against its sector by falling further than the sector.

The checkpoint's comparisons against SPY, the stop test's included, use the
SPY measure.

**The checkpoint.** On 2026-12-31, or the first day after it with at least
**40**<!--rule:strategy_checkpoint_min_pm_calls--> resolved PM calls:

- **Keep** if the PM's calls beat SPY on mean excess **and** the paper book's
  return at least matched SPY's over the same period.
- **Stop Claude-directed entries** if the PM's calls trail SPY **and** trail
  the average pitch — the PM is subtracting value; what follows is a
  conversation with Chris.
- **Rework** otherwise.
- Any analyst with **15**<!--rule:strategy_analyst_review_min_pitches-->+
  resolved pitches and negative mean excess against its benchmarks is dropped
  or rebuilt.

The paper book's return is measured from its **start date: the day of the
first PM run that actually dispatches** (a PM fire skipped as blind, outside
its window or with no runner starts nothing), with SPY's return taken from
that session's close.

This is a decision rule, not a significance test: bootstrap intervals are
shown once a group has **20**<!--rule:strategy_scorecard_ci_min_n-->
resolved items, for honesty, not as the gate. The rule changes only by a
recorded amendment quoting Chris. `CLAUDE.md` §3.6's halt protects the money
throughout, independently.

## 9. Session protocol and deadman checks

The engine runs unattended; a session is a human or Claude looking in.

**Open:** `CLAUDE.md` §4.5 reconciliation (the engine's latest reconcile and
`/health`), `scripts/check-consistency.sh` and `tc check-consistency` (a FAIL
is a defect to fix before anything else), §3.6 from the latest session close,
`ALERT.md`. Then the desk:
- `/health`: `blind`, `token_days_until_dead`, `runner_ok`.
- `job_runs` for the last day: every desk job `done` or `noop`, none
  `failed`; the 07:30 expectations digest says the same in Discord.
- `/api/scorecard`: the checkpoint status and any analyst flag.

**Deadman checks** — absence of action is never evidence nothing needed
doing (`CLAUDE.md` §0):
- No `🗂️ DESK evening` post by 18:30 ET on a trading day → the chain did not
  run.
- No PM summary by 10:50 ET → the PM missed its window; no entries that day.
- `bars_refresh` `failed` → no scoring that evening; it catches up next time.

## 10. The paper book and going live

The paper book starts at the first PM run that actually dispatches, with the
account's value as cash (legacy positions count as cash), and applies every
cap above. Vetoed proposals still fill in it, flagged, because it records
Claude's decisions.

Two things the paper book does not model, stated so nobody reads them into
it:

- **No settled/unsettled split** (`CLAUDE.md` §5). A sale's proceeds are
  cash at once, so a same-day round trip that would be a good-faith
  violation at Schwab is legal in paper. Revised Plan 1's settlement gates
  are therefore stricter than the book they will be compared against.
- **No §3.6 halt analogue.** The paper book never halts on its own
  drawdown. The real account's `CLAUDE.md` §3.6 halt governs the real
  money; the checkpoint's stop test (§8) is what judges the paper record.

Real orders are switched on only when all three hold:

- revised Plan 1 passes its own paper end-to-end test;
- the desk has run about two weeks of clean paper days;
- the §9 amendment in spec §12.3 is committed with Chris's words quoted.

The paper book keeps running after that, so the record has no gap.

## 11. Open items

1. The `CLAUDE.md` §9 amendment for the veto window (spec §12.3) — needs
   Chris's words; not needed for the paper phase.
2. Revised Plan 1 (spec §11).
3. Legacy positions: the PM attaches a call to each or recommends an exit;
   Chris acts on legacy exits at Schwab.

## 12. What changed on 2026-09-27, and why

Six weeks produced four stock entries and no option trades (spec §1). Removed
from this playbook, all strategy rules, none needing §9: the core, catalyst
and options sleeves; the δ 0.50 long-premium floor; the earnings-timed option
entry and expiry windows; the scout escalation requirement and the HOT
checklist (including its confirmed-earnings-date requirement, which §3.7 had
already made context rather than a gate); the stall and ratchet exits
(replaced by target, invalidation and horizon); the 3-whole-share catalyst
granularity; the NVDA-week guard; the research alert cap. The retired keys are
guarded against returning by `engine/tc/rules/consistency.py`.

---

*Revision history for this document — and for `CLAUDE.md` — is in
`CHANGELOG.md` at the repository root.*
