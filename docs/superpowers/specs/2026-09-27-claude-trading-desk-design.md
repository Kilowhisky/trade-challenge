# Claude Trading Desk — design

**Status:** design approved section by section in conversation 2026-09-27;
spec pending Chris's review; not yet implemented. Replaces the *strategy* —
`strategy.md`, the strategy section of `rules.yml`, the research prompts and
the 2026-08-30 information-edge scout design — and keeps the v3 *runtime*
(`2026-09-02-v3-engine-architecture-design.md`) it runs on. One `CLAUDE.md`
change is needed before real orders are switched on; §12.3 scopes it, and
its wording comes from Chris per `CLAUDE.md §9`.

**Reference convention:** `CLAUDE.md §X` is a rule in the trading manual;
`v3 §X` is a section of the v3 engine spec; a bare `§X` is a section of
*this document*.

---

## 1. Why this document exists

Between 2026-08-13 and 2026-09-27 the account made **four stock entries and
no option trades**. Two of the four stopped out. On 2026-09-25 the account
closed −2.9% from its high-water mark with about 12% of it invested. A
review of the engine's database and research documents on 2026-09-27 found
five causes, ranked by how much of the inactivity each explains:

| # | Cause | Evidence |
|---|---|---|
| 1 | **No way to place an order.** v2 was retired 2026-09-07. v3 has no order path: Plan 1 is 0 of 16 tasks, `DECIDE_TOOLS` is empty, and no job consumes a HOT row or an escalation — `dispatch.py` only posts them to Discord. Before 09-04, v2 had an executor but no scheduled writer of HOT rows. | `HANDOFF.md`; `engine/tc/mcp/registry.py:55`; `engine/tc/jobs/dispatch.py:420-450` |
| 2 | **A funnel of vetoes with no rule that says yes.** 93% of screened rows were never measured, because a full hand-verified checklist does not fit a research pass's read budget. Several gates bind that the manual does not impose: a company-confirmed earnings date (`CLAUDE.md §3.7` says the date gates nothing); §3.8 read as a same-sector ban rather than a 50% cap; a position closed 09-16 kept blocking its whole sector for 8+ sessions. | job_runs verdicts; `.claude/commands/research.md` §C |
| 3 | **The option rules leave almost nothing tradeable at this account size.** A δ ≥ 0.50 contract under the 10% premium cap needs an underlying below about $55–75, and those names fail open interest (80% of 122 in-band observations) or spread (65%). The earnings-timed entry and expiry windows remove most of the rest. Options also required a scout escalation, and the scout observed 3 names a day from a cohort that grew to 215, while a first observation never counts as a delta. **19 names laddered, 0 takeable; 13 scout runs, 0 escalations.** | `research/options-roster.md`; job_runs scout verdicts; `.claude/commands/scout.md` §B2–§C |
| 4 | **What it did like, it picked badly.** The three HOT names (MT, S, GIS) fell a mean 5.8% after promotion, and eight WATCH names fell a mean 4.5%, over windows in which SPY rose. The system's own scorecard shows its proximity screen lagging SPY. Loosening the gates alone would add negative-expectancy trades. | engine DB; `research/scorecard.md` |
| 5 | **Effort went into documents, not decisions.** 68 of 76 research passes found nothing new. `candidates.md` was rewritten 88 times, and its owed-items list reached #83. Two of the three HOT verdicts were silently dropped as `content_failed` because the model wrote a price as a number into a `str` field (`HotFresh.ref`, `engine/tc/jobs/spec.py:57`). | job_runs; engine DB `artifacts` |

In one line: every layer could say no, nothing was responsible for saying
yes, and the part that places the order was never built.

## 2. Decisions made by Chris on 2026-09-27

This document does not relitigate these decisions.

1. **Purpose: test Claude as a trader.** Success means learning whether
   Claude's own judgement has an edge over SPY. Claude picks the trades.
2. **Style: swing trader.** Holds from days to about four weeks, in
   stocks, ETFs and 30–60 day long options, making several calls a week.
3. **Shape: analysts plus a portfolio manager.** Chris chose this over a
   single daily decision loop (approach A, §17).
4. **Analysts split by approach:** technical, earnings, news/catalyst and
   macro/ETF.
5. **Approval: a 10-minute veto window on entries.** A vetoed or expired
   proposal is scored as a paper trade. This requires a `CLAUDE.md §9`
   amendment in Chris's words before real orders run (§12.3).
6. **Budget: one shared subscription with scheduled runs.** Heavy analyst
   work happens outside market hours; only the PM runs in them.
7. **The verdict is a checkpoint at 3 months** (§8), fixed before any
   result exists.

## 3. Principles

1. **Claude decides; the engine checks and acts.** Every rule in the manual
   is enforced by engine code at proposal time, not by a prompt checklist.
   The model's budget goes to judgement.
2. **Every decision is a scored prediction.** Every pitch and every call,
   funded or not, is scored. Throughput of scored decisions is what makes
   the test readable, so the desk is required to commit, not merely allowed
   to.
3. **No price is ever typed by the model.** Reference prices, fills and
   benchmark levels are stamped by the engine from broker reads. This
   removes the `HotFresh.ref` class of failure.
4. **Structured records, not prose documents.** Analysts and the PM write
   typed rows through validated tools. No job maintains a Markdown file.
5. **Budget is a design constraint.** The server shares Chris's
   subscription (the 2026-09-02 session-limit outage). Run placement,
   models and turn limits are chosen for it.
6. **Unamendable core untouched.** `CLAUDE.md §1.1`, `§1.2`, `§3.1` and
   `§3.6` bind exactly as written.

## 4. The desk: roles and daily schedule

All times are ET. Claude jobs run through the existing runner, one at a
time.

| Time | Job | Kind | What it does |
|---|---|---|---|
| 16:10 | `bars_refresh` | engine code | Daily bars for the liquid universe (the weekly sweep's ranked set), the ETF list (§4.2), and every symbol with an open pitch, call or position. Then scoring (§7). |
| 16:30 → ~18:10 | `analyst_technical` → `analyst_earnings` → `analyst_news` → `analyst_macro` | Claude, Sonnet | A **chain**: each starts when the previous ends. Each reads its briefing (§4.1) and web search, and files at most 5 pitches (§5). |
| 08:00 → ~08:20 | `desk_preopen`: news, then earnings | Claude, Sonnet, short mode | Overnight news and pre-market reports. At most 2 new pitches each; may withdraw its own pitches. |
| **09:50** | `pm` | Claude, Opus | Reviews the book, ranks open pitches, makes up to 5 calls and decides funding (§6). The one heavy Claude run during market hours. |
| 12:30 | `pm_midday` | Claude, Opus, light | Held positions and today's news only: exit, tighten or hold. **No new entries.** |
| every 15 min 09:32–15:47 | `tick` | engine code | Unchanged watches, plus the automatic exits in §9.5. |
| Sat 07:40 | `weekly_universe` | engine code | Unchanged. |
| Sat 08:30 | `scorecard_weekly` | engine code | Posts the scorecard (§7.3) to Discord. |

That makes **two Claude runs during market hours instead of today's
seven or eight.** Chris's own build sessions stay outside about 09:45–12:45
ET rather than the whole trading day.

**Models** are set by each agent's frontmatter (`model:`), which the runner
already honours (`runner/tc_runner/app.py` passes `agent` through). Analysts
use `sonnet`, because they mostly search and summarise. The PM uses `opus`,
because it is the trader under test.

**Chain and queue.** `dispatch.py` currently records `noop` /
`"runner busy"` when the runner is occupied; the 2026-09-08 catalyst run was
lost that way. Desk jobs **queue** instead: the engine holds a pending run
until the runner is free. `pm` has priority over every other Claude job. A
queued job still has a deadline (§13).

### 4.1 Briefings

A briefing is computed by engine code from stored bars and broker reads and
served by the `briefing` tool. The model adds judgement — why a move
matters, what comes next, where the thesis is wrong — not measurement. Each
row carries price, ATR%, 20/50/200-day trend statistics, 3- and 6-month
relative strength against SPY, distance from the 52-week high and low, and
an `option_band` flag (price ≤ $100 or an ETF on the list, as a hint only).

| Analyst | Briefing contents |
|---|---|
| technical | 52-week breakouts and breakdowns on volume; pullbacks to the 20- or 50-day average inside an uptrend; top and bottom relative-strength deciles; volume spikes ≥ 2.5× the 20-day average |
| earnings | Names that gapped ≥ 3% on ≥ 2× volume in the last 3 sessions. Upcoming report dates come from the analyst's own web search, recorded in the pitch evidence, because the FMP calendar was unreachable for 86 consecutive runs. |
| news | Schwab movers for the major indices and the day's gap list from bars |
| macro | Trend and relative strength across the ETF list (§4.2), plus SPY, TLT, UUP and USO context rows |

Each analyst's briefing also includes **its own last 10 resolved pitches**
with outcomes (the `my_record` tool).

### 4.2 ETF list

This is a fixed list in `rules.yml`, changeable as a strategy rule. It holds
SPY, QQQ, IWM, DIA, the 11 sector SPDRs (XLB, XLC, XLE, XLF, XLI, XLK, XLP,
XLRE, XLU, XLV, XLY), SMH, XBI, KRE, XHB, ITB, JETS, GLD, SLV, GDX, TLT,
IEF, HYG, EEM, FXI, EWZ and USO. There are no leveraged or inverse ETFs
(§9.1).

## 5. Pitches

An analyst files a pitch through `pitch_submit`. The engine validates it
and refuses with an actionable message, using the existing refusal
pattern.

| Field | Rule |
|---|---|
| `analyst` | Set by the engine from the job; never supplied by the model |
| `symbol` | Must be in the weekly universe's qualified set or on the ETF list |
| `direction` | `up` or `down` |
| `thesis` | At most 400 characters: what happens next and why |
| `evidence` | 1–5 items `{url, claim, date}` |
| `target`, `invalidation` | Prices. Target on the called side of the reference, invalidation on the other side; both within `pitch_level_max_distance_pct` (30%) of the reference |
| `horizon_days` | 2–20 trading days |
| `conviction` | 1–5 |
| `benchmark` | One of SPY or the 11 sector SPDRs |

- **The reference price is engine-stamped.** Pitches filed in the evening
  and pre-open are priced at the next regular session's opening print from
  bars. The engine stamps SPY's and the benchmark's open alongside. Target
  and invalidation are re-checked against that reference once it exists. A
  pitch whose levels are no longer on the correct sides (the open gapped
  through one) is **resolved at the open**, not voided, so a stale pitch
  cannot escape scoring.
- **Limits:** at most 5 pitches per analyst run, and at most 2 new ones per
  analyst in `desk_preopen`. **One open pitch per analyst per symbol and
  direction**; re-pitching an open one is refused with its id.
- **Withdrawal:** `pitch_withdraw` works only before 09:30 on the pitch's
  reference session (in practice at the 08:00 run). The reference is
  stamped at 16:10 but is the 09:30 print, so the cutoff is the open, not
  the stamping. A withdrawn pitch is logged and not scored. After the
  open, a pitch can only resolve.

## 6. The PM

The `pm` run (09:50) must return a `PmVerdict` containing:

1. **Held positions:** for each, `hold`, `exit` or `tighten` with a
   one-line reason. `tighten` sets a new invalidation, which may only move
   closer to the current price, reducing risk.
2. **Calls:** at most `desk_max_new_calls_per_day` (5) **new** calls. Each
   either adopts a pitch (by id, optionally adjusting target, invalidation
   or horizon, with the change recorded) or originates one (`origin: pm`).
   Calls follow the pitch field rules. **One open call per symbol and
   direction**; reaffirming an open call is not a new call. Each call's
   reference price is the live quote at `call_submit` time, stamped with
   SPY's and the benchmark's quotes.
3. **Funding** for each new call: `none`, `shares`, `call` or `put`, sized
   per §9.2. `call`/`put` requires a contract returned by
   `option_candidates` (§9.3).

Tools: `book`, `pitches_read`, `scorecard`, `quotes`, `price_history`,
`option_candidates`, `call_submit`, `call_extend`, `exit_request`, plus web
search. There are no order tools: in paper mode `call_submit` funding feeds
the paper book (§10); after the order path lands it feeds `propose_entry`
(§11).

`pm_midday` (12:30) gets the same tools except `call_submit`. It may exit or
tighten only.

**Extension:** a funded call that resolved at the previous close (§7.1)
normally means its position exits this morning (§9.5). `call_extend` at
09:50 instead opens **one new call** on the same symbol and direction, with
a new reference, levels and horizon, and the position carries over. The old
call's resolution stands. It may be used once per original call.

## 7. Scoring

Scoring is engine code, run inside `bars_refresh` after the close. It is
append-only and can be recomputed from stored bars.

### 7.1 Resolution

A pitch or call resolves at the first of:

- **Target touched:** high ≥ target (up) or low ≤ target (down). Resolves
  at the target.
- **Invalidation touched:** resolves at the invalidation.
- **Horizon reached:** resolves at that session's close.

The edge cases:

- **Same-day double touch:** if both levels are touched in one bar, it
  counts as an invalidation (the conservative reading).
- **Gap through a level:** resolves at that session's open.
- **Missing bars:** the item stays open and is flagged in the expectations
  digest after 3 sessions.

### 7.2 Metrics

- **Return:** the direction-signed percentage from reference to resolution.
- **Excess:** return minus SPY's return over the same window, and
  separately minus the benchmark's.
  - The window starts at the stamped reference (open or intraday quote).
  - A level resolution uses the resolution day's close for SPY and the
    benchmark. With daily bars the exact touch time is unknown, and the spec
    accepts this approximation.
- **Hit:** target before invalidation.
- **Funded trades:** scored on money as well — actual fills and option
  profit and loss, or simulated fills in the paper book (§10), flagged.

### 7.3 Scorecard

The scorecard is served at `/api/scorecard` and posted to Discord every
Saturday. For each group — each analyst's pitches, all pitches, the PM's
calls, and funded trades (paper and real separately) — it shows the count,
hit rate, mean return, and mean excess against SPY and against the
benchmark. Once a group has ≥ `scorecard_ci_min_n` (20) resolved items it
also shows a 95% bootstrap interval. It adds:

- **PM selection edge:** the PM calls' mean excess minus all pitches' mean
  excess.
- **Book vs SPY:** the paper book and the real account against buy-and-hold
  SPY from the start date (§8).

The PM sees the whole scorecard, and each analyst sees its own record
(§4.1).

## 8. The checkpoint rule

This rule is pre-registered: it is copied into `strategy.md` before the first
call and changes only by a recorded amendment that quotes Chris.

- **Start date:** the day the first PM call is scored.
- **Checkpoint:** 2026-12-31, or the first day after it on which the PM has
  ≥ 40 resolved calls.
- **Keep** if the PM calls' mean excess over SPY is > 0 **and** the paper
  book's return ≥ SPY's total return over the same period. (The paper book
  holds every funding decision, vetoed ones included, so it is Claude's
  consistent record. The real account is reported alongside.)
- **Stop Claude-directed entries** if the PM calls' mean excess over SPY is
  < 0 **and** below the all-pitches mean. The PM is then subtracting value,
  and what follows is a conversation with Chris.
- **Rework** in every other case.
- **Analysts:** any analyst with ≥ 15 resolved pitches and negative mean
  excess against its benchmarks is dropped or rebuilt.
- **This is a decision rule, not a significance test.** 40 overlapping
  calls cannot prove an edge. The bootstrap intervals are reported for
  honesty, not used as the gate.
- The `CLAUDE.md §3.6` halt protects the money throughout, independently of
  the checkpoint.

## 9. Positions

### 9.1 Instruments

- **Up call:** shares, an ETF from the list, or a long call.
- **Down call:** a long put. The cash account cannot short, and leveraged or
  inverse ETFs are not used *(strategy rule)*.
- **Conviction 1–2:** never funded. These calls are scored only.

### 9.2 Sizing *(strategy rules, inside the manual's caps)*

| Conviction | Shares/ETF (% of account value) | Option premium (% of account value) |
|---|---|---|
| 3 | 10 | 5 |
| 4 | 15 | 7.5 |
| 5 | 20 | 10 (= `CLAUDE.md §3.2` cap) |

- At most **8** funded positions. Legacy holdings (§9.6) count toward the
  8.
- Total cash stays ≥ **$900.00**. The reserve invariant is unchanged.
- Open option premium stays ≤ 30% (`CLAUDE.md §3.2`).
- The **6% daily-ATR ceiling for share funding** is kept, so a noisier name
  can be expressed only in options.
- **§3.8 as written:** at proposal time the engine computes each candidate's
  60-day correlation with held names from stored bars and applies the 50%
  cap to the correlated cluster (same sector or theme, or ρ > 0.7). It is a
  cap, never a same-sector ban.

### 9.3 Option contract selection

`option_candidates(symbol, direction, horizon_days, conviction)` pulls the
**live** chain (the PM runs in market hours; post-close spreads are not
decision-grade) and returns up to 3 contracts that clear every
`CLAUDE.md §3.2` floor:

- **Expiry:** days to expiration ≥ max(18, the horizon in calendar days +
  the §3.3 close at 5 DTE + 7), and ≤ `option_max_dte` (60).
- **Delta:** within the manual's band of 0.45–0.75, ranked by distance from
  `option_target_delta` (0.60).
- **Open interest and spread:** OI ≥ 500 and spread ≤ 10% of mid.
- **Premium:** within the conviction's cap (§9.2).

The engine builds the option symbol. If nothing clears, the call is funded
as shares (up calls only) or stays unfunded, and the refusal is logged with
its binding floor.

### 9.4 Entries

- The PM sets `max_entry_price`. The engine places a **day limit** at the
  ask, capped at that price.
- The Discord proposal shows the symbol, instrument, size, limit, stop,
  target, horizon and thesis. It executes after the veto window (10
  minutes) unless Chris reacts ❌; ✅ executes at once.
- The same posts and reactions run during the paper phase, so vetoes are
  recorded from day one. There the window is the strategy key
  `veto_window_minutes`; the §12.3 amendment replaces it with
  `manual_entry_veto_window_minutes` once it governs real orders.
- **At execution** the engine re-quotes. If the ask is above
  `max_entry_price`, it skips and logs the order, and the call is still
  scored.
- Proposals only go out between 10:00 and 15:00. Unfilled entries are
  cancelled at 15:55 (`CLAUDE.md §4.2`).

### 9.5 Exits

Exits are **automatic engine code, with no veto.** Closing orders only
reduce risk (`CLAUDE.md §3.6`: "closing orders are always permitted").
Each posts a Discord notice.

**Shares and ETFs:**

- **Stop:** a GTC stop-limit is placed immediately after the fill.
  - Its trigger is the **higher** of the `CLAUDE.md §3.4` formula level and
    the call's invalidation. §3.4 allows a trigger to be raised, never
    lowered, so a thesis with a tight invalidation gets a tight stop.
  - The limit sits 5% below the trigger, per §3.4.
  - The PM may tighten the stop afterwards, never loosen it.
- **Target reached** (on a tick): cancel the stop first (`CLAUDE.md §4.7`),
  then place a limit sell at the bid.
- **Call already resolved:** a funded position whose call resolved at the
  previous close exits at the first tick after 10:00, unless
  `call_extend` ran at 09:50 (§6).
  - This covers the horizon.
  - It also covers a target or invalidation touched between two ticks,
    which the bars saw but no tick did.
  - So a funded position never outlives its call by more than one night.

**Options** (no resting stops, per `CLAUDE.md §3.4`):

- On each tick the engine watches the **underlying**. It closes the
  position when the underlying crosses the invalidation or reaches the
  target.
- A call already resolved at the previous close is handled exactly as for
  shares.
- The §3.3 close at 5 DTE applies as today.
- The closing limit goes in at the mid price. After 15 minutes unfilled it
  is re-priced to the bid, which stays within the `CLAUDE.md §4.10`
  per-symbol ceiling.

### 9.6 Legacy holdings

CSX and IQV predate the desk and keep their resting stops. At the PM's
first run it must attach a call (target, invalidation, horizon) to each or
recommend an exit. Until the order path exists, an exit recommendation is
posted for Chris to act on at Schwab. Legacy positions are excluded from the
checkpoint (§8).

## 10. The paper book

The paper book runs from the start date, before and after orders go live.

- **Starting capital:** equity equal to account value on the start date,
  with the legacy positions treated as cash.
- **Same rules as real money:** §9 sizing against paper equity, the $900
  reserve and all caps.
- **Fills and exits:**
  - Entry: at the ask at the moment the veto window would close, with the
    same `max_entry_price` skip rule.
  - Stops: filled at the trigger, or at the next tick's price if the market
    gapped below the limit (the `CLAUDE.md §3.4` gapped-stop exit).
  - Target, horizon and option exits: filled at the bid on the tick that
    fires them.
- **Vetoed proposals stay in the paper book**, flagged `vetoed`, because it
  records Claude's decisions rather than Chris's.
- **Blind days:** when the engine is blind, the paper book does nothing,
  exactly as the real one would.

## 11. The order path (dependency)

Real orders need Plan 1 (`docs/superpowers/plans/2026-09-08-v3-plan1-order-path.md`),
**revised** before anyone executes it:

1. **Reconcile it with Plan 0c's code.** It plans to create
   `mcp/__init__.py`, `jobs/spec.py` and `ROLE_TOOLS`, all of which already
   exist.
2. **Replace ✅/❌-to-execute with the veto window** (§9.4). Approval rows
   gain a `veto_deadline`; the persisted-approval re-arm on restart (v3
   §5.4) carries over unchanged.
3. **Replace the `decide` job** with `pm`/`pm_midday`. `call_submit`
   funding becomes `propose_entry(EntryIntent)`, and `exit_request` becomes
   `propose_exit`.
4. **Add the automatic exits in §9.5** to Task 10 (`loops/stops.py`) and
   Task 12 (trips that act). This covers the stop trigger as max(formula,
   invalidation), target exits and horizon exits.
5. **Keep the ~22 gates, idempotency and state machine** (v3 §5.2–§5.5)
   unchanged.

## 12. Rules

### 12.1 Playbook rules removed *(strategy rules; no §9 needed)*

- The core, catalyst and options sleeves and their ceilings.
- The δ ≥ 0.50 long-premium floor.
- The earnings-timed option windows (`scout_entry_window_*`,
  `option_expiry_*_days_past_earnings`).
- The scout-escalation requirement and the HOT checklist, including its
  confirmed-earnings-date requirement.
- The stall and ratchet rules, replaced by target, invalidation and
  horizon.
- `catalyst_min_whole_shares`.
- The NVDA-week guard.
- The 2-per-day research alert cap.

### 12.2 Rules kept or added (`rules.yml`)

- **Kept:** the $900 reserve invariant, the 6% ATR ceiling for shares, and
  every `manual_` key.
- **New `strategy:` keys:**

| Key | Value |
|---|---|
| `desk_max_pitches_per_analyst_run` | 5 |
| `desk_preopen_max_new_pitches` | 2 |
| `desk_max_new_calls_per_day` | 5 |
| `pitch_horizon_min_days` / `pitch_horizon_max_days` | 2 / 20 |
| `pitch_level_max_distance_pct` | 30 |
| `min_fundable_conviction` | 3 |
| `size_shares_pct_conviction_3` / `_4` / `_5` | 10 / 15 / 20 |
| `size_option_premium_pct_conviction_3` / `_4` / `_5` | 5 / 7.5 / 10 |
| `max_funded_positions` | 8 |
| `option_max_dte` | 60 |
| `option_target_delta` | 0.60 |
| `option_exit_reprice_minutes` | 15 |
| `veto_window_minutes` | 10 (paper phase; replaced by `manual_entry_veto_window_minutes` per §12.3) |
| `entry_window_start` / `entry_window_end` | 10:00 / 15:00 |
| `checkpoint_date` | 2026-12-31 |
| `checkpoint_min_pm_calls` | 40 |
| `analyst_review_min_pitches` | 15 |
| `scorecard_ci_min_n` | 20 |
| `etf_list` | §4.2 |

- The consistency checker (`engine/tc/rules/consistency.py`,
  `scripts/check-consistency.sh`) is updated so that `strategy.md` markers
  and these keys agree. The removed keys must stay absent, following the
  practice set when `all_options_flat_by` was deleted.

### 12.3 Manual amendment (before real orders; Chris's words)

- **Scope:**
  - `CLAUDE.md §0` ("Unattended is not unapproved. Every order still passes
    the Discord approval gate").
  - *Verified account facts* ("Every write call blocks on Chris's ✅/❌").
- **New meaning:** an **entry** order executes after a 10-minute veto
  window unless Chris reacts ❌. Protective stops, `§3.3`/`§3.5` forced
  closes and exits that carry out a call's recorded target, invalidation or
  horizon run automatically and are announced.
- **New key:** `manual_entry_veto_window_minutes: 10` in `rules.yml`, with a
  `<!--rule:-->` marker.
- **Optional housekeeping in the same amendment:** the header's references
  to `tick.md §B5` and `scripts/*.sh` (`HANDOFF.md`, "Needs you" item 2).
- **§9.3 check:** this amendment is not a reaction to a losing position.
  The book is flat, and the change concerns throughput and the validity of
  the test.
- **Chris's message is quoted verbatim in the commit body** (`CLAUDE.md
  §9.2`). A multiple-choice selection in the design conversation does not
  substitute for it.

## 13. Engine components

**New tables** (append-only where they are ledgers):

- `bars`
- `pitches`
- `calls`
- `resolutions`
- `paper_orders`
- `paper_positions`
- `desk_queue`

**New MCP tools by role:**

| Role | Tools |
|---|---|
| analyst (replaces `research`) | `briefing`, `pitch_submit`, `pitch_withdraw`, `my_record`, `quotes`, `price_history`, `instruments`, `get_datetime`, `market_hours` + WebSearch/WebFetch |
| pm (the v3 `decide` role) | `book`, `pitches_read`, `scorecard`, `quotes`, `price_history`, `option_chain`, `option_candidates`, `call_submit`, `call_extend`, `exit_request`, `get_datetime`, `market_hours` + WebSearch/WebFetch |

**New jobs** (`config.yml` and `engine/tc/jobs/spec.py`), with verdict
models and budgets:

| Job | Timeout | Max turns |
|---|---|---|
| `analyst_*` | 1500 s | 40 |
| `desk_preopen` | 600 s | 20 |
| `pm` | 900 s | 40 |
| `pm_midday` | 300 s | 15 |

`bars_refresh` and `scorecard_weekly` are engine code.

**Queue deadlines:**

| Job | Latest start |
|---|---|
| `pm` | 10:30 |
| `pm_midday` | 13:30 |
| `desk_preopen` | 09:00 |
| evening chain | 20:00 |

A job that has not started by its deadline is recorded as `missed`.

**Prompts:** `.claude/agents/analyst-{technical,earnings,news,macro}.md`
(`model: sonnet`) and `.claude/agents/pm.md` (`model: opus`), plus matching
`.claude/commands/`.

**Discord posts** (engine-formatted from rows; no model prose required):

- An evening desk summary (pitches per analyst).
- The PM decision and proposals.
- Fills, stops and exits.
- The weekly scorecard.

**Retired** (tombstoned, as the v2 files were; history tables remain
readable):

- **Jobs:** `scout`, `catalyst`, `research`, `preopen`, `postclose`,
  `sector_tag`.
- **Documents:** `candidates.md`, `standing.md`, `options-roster.md` and
  `scorecard.md`.
- **Code:** the cohort/sector join.
- **Kept:** `tick`, `session_close`, `token_check`, `expectations`,
  `backup` and `weekly_universe`.

## 14. Failure handling

| Failure | Behaviour |
|---|---|
| An analyst fails or times out | The chain continues with the next analyst. The PM uses whatever pitches exist. The gap shows in the 07:30 expectations digest. |
| Runner busy | Queue, not `noop`. The PM has priority. Past its deadline the job is `missed`, and for the PM that means no entries that day, logged. |
| Session limit | One Discord line. `pm` retries once after 15 minutes; nothing else retries. |
| Blind (token dead) | No bars, no PM, no paper fills. Scoring catches up from bar history when sight returns. |
| Tool validation refusal | Returned as text the model can act on in the same run (existing pattern). |
| Verdict parse failure | `content_failed`, as today. Rows already written through tools stand, because the record lives in the tables, not in the verdict. |
| Missing bars for a symbol | The item stays open and is flagged after 3 sessions. |
| Budget overrun | Recorded in `job_runs`. A per-week total of runner minutes appears in the scorecard, so the shared subscription's load is visible. |

## 15. Testing

The repo's existing practice applies: write the test first, mypy
`--strict`, ruff, and both consistency checkers.

- **Property tests (hypothesis):**
  - Resolution order, gaps and same-day double touches.
  - Excess-return arithmetic.
  - Conviction sizing against caps and the reserve.
  - Stop trigger = max(formula, invalidation), never below the formula.
  - Paper-book fills.
- **Validator tests:** the pitch and call validators against near misses,
  including a price supplied by the model, which must be refused.
- **`option_candidates`:** tested against recorded real chains (the
  redacting recorder, v3 Phase 0a). It must refuse every floor violation
  seen in the 2026-09 options roster.
- **Contract tests:**
  - The analyst role cannot name `call_submit`, `propose_*` or any order
    tool.
  - The pm role cannot name an order tool.
  - The existing forbidden-name regex still holds.
- **A full-day replay:** evening chain → pre-open → PM → ticks →
  `bars_refresh` against the fake broker with recorded fixtures, asserting
  the tables at each stage.
- **The paper phase is the live integration test.** Orders are switched on
  only under §16's gate.

## 16. Sequencing

1. **The desk, on paper.** This spec's first implementation plan. When it
   lands, the start date (§8) is set and the 3-month test begins.
2. **Plan 1, revised** (§11), as its own plan.
3. **Orders switched on** only when all three hold:
   - (a) revised Plan 1 passes its paper end-to-end test;
   - (b) the desk has run about 2 weeks of clean paper days (every job
     `done`, scoring reconciling);
   - (c) the §12.3 amendment is committed with Chris's words quoted.

   The paper book keeps running, so the record never has a gap.

Build sessions run outside market hours
(`memory: claude-session-limit-starves-server`).

## 17. Considered and rejected

| Option | Why not |
|---|---|
| A: one daily PM loop over an engine-built shortlist | Cheaper and simpler. Chris chose B for research depth and per-analyst attribution. |
| C: rule-generated signals with Claude as filter | Tests Claude as an editor, not a trader, against purpose (§2.1). |
| Analysts by sector, or Chris's three sectors plus a generalist | Same reasoning style in every seat. The scorecard would say which sector did well, not which kind of judgement works. |
| A separate Claude account for the server | Declined for cost. Revisit if session-limit failures show in `job_runs` or when orders go live. |
| Monthly review with no end date; early-stop rule | Too easy to explain away, or too likely to stop on noise. The fixed checkpoint was chosen. |
| Keeping the information-edge scout | 13 runs and 0 escalations. Structurally starved (§1, cause 3), and its thesis belongs to Chris rather than Claude. |
| Loosening the old gates | The names the old funnel liked lagged SPY (§1, cause 4). More of them is not an improvement. |

## 18. Assumptions to verify early

1. **Bars throughput.** Can ~550 `price_history` calls fit inside the
   16:10 window under Schwab's rate limit? If not, shrink the set to the
   top-ranked names plus open items.
2. **Live chains at 09:50** carry the bid, ask, OI and greeks that
   `option_candidates` needs, for ETFs and sub-$100 names.
3. **Queueing.** Is engine-side queueing enough without runner changes?
   The runner is "one job at a time" (`4b16e5e`).
4. **Model choice.** `model: sonnet` in agent frontmatter produces Sonnet
   runs through the Agent SDK path the runner uses.
5. **Subscription load.** Measure the evening chain's use of the shared
   subscription in week one.
6. **Earnings calendar reachability.** Web search can reach calendars
   (Nasdaq, company IR) where FMP could not.
