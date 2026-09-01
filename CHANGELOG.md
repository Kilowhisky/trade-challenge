# Changelog

History for `CLAUDE.md` (the operating manual) and
`strategy.md` (the
playbook).

**This file is not read during a session.** It exists so those two documents
can state only what is currently true, without carrying their own history in
the context that loads every time. Read it when you need to know why a rule
says what it says, or what value it used to carry.

Manual changes go through §9: an explicit conversation Chris initiates,
recorded as a commit with his own message quoted verbatim, and never in
reaction to a losing position. Playbook rules marked
*(strategy rule)* are discretionary and change without §9.

---

## Manual — `CLAUDE.md`

### 2026-08-31 · The competition rule set is retired (§9 amendment)

Chris withdrew from the competition on 2026-08-29. In his words:

> "The competition is over, i'm dropping out."

and, on §3.7:

> "Ditch 3.7 entirely"

— scoped in the same conversation to *"Kill earnings bar, keep halt rule
only."* Manual version v3 → **v4**. Title changed from "Trading Competition —
Operating Manual" to "Trading Account — Operating Manual".

**Parameter changes** (`rules.yml` and `CLAUDE.md` amended in the same commit
per the file's CONTRACT):

| Key | Before | After |
|---|---|---|
| `manual.option_single_position_pct` | 20 | **10** |
| `manual.option_min_delta` | 0.35 | **0.45** (now a band floor) |
| `manual.option_max_delta` | *(did not exist)* | **0.75** |
| `manual.window_start` | 2026-08-14 | **deleted** |
| `manual.window_end` | 2026-09-14 | **deleted** |
| `manual.final_session` | 2026-09-14 | **deleted** |
| `manual.lockout_start` | 2026-09-10 | **deleted** |
| `manual.lockout_final_sessions` | 3 | **deleted** |
| `strategy.option_min_delta` | 0.40 | **0.50** |
| `strategy.all_options_flat_by` | 2026-09-04 | **deleted** |
| `strategy.last_leveraged_entry` | 2026-09-01 | **deleted** |
| `strategy.option_expiry_min_days_past_earnings` | *(new)* | **14** |
| `strategy.option_expiry_max_days_past_earnings` | *(new)* | **28** |
| `strategy.scout_entry_window_min_days` | *(new)* | **21** |
| `strategy.scout_entry_window_max_days` | *(new)* | **42** |

**§8 scoring and endgame — deleted.** It defined a winner, a final session, a
3-day new-position lockout, and an all-options-flat date. A scoring rule with
no contest still constrains trades: the lockout would still refuse entries and
the flat-by date would still force closes, in service of a deadline that no
longer exists. The section number is retained as a tombstone so older `status/`
notes and commit messages that reference "§8" resolve to an explanation rather
than to a renumbered rule.

**§3.7 event risk — reduced to the halt rule.** The earnings prohibition ("no
position may be held through a scheduled earnings report") is removed for all
instruments. It categorically outlawed the only strategy this account has a
demonstrated edge in — see
`docs/superpowers/specs/2026-08-30-information-edge-scout-design.md` §2. The
corporate-action **exit** requirement became a stop **re-pricing**
requirement: same arithmetic protection (a stop priced before a split is
meaningless afterward), without forcing a close on exactly the merger theses
the amendment exists to permit. The halt provision is unchanged.

**§3.2 delta floor → band, 0.45–0.75.** A floor alone is the wrong shape for
buying options into a scheduled event. IV collapses after the print and that
crush destroys *extrinsic* value, so an ITM contract survives it: below 0.45 is
a lottery ticket bought into a known volatility collapse, and above 0.75 is
paying option spreads to own something that already behaves like the stock.

**§3.2 single-thesis premium cap 20% → 10%.** Fractional-Kelly grounds: the
edge is unvalidated (design spec §8.1 — two remembered winners cannot
distinguish edge from survivorship), total loss of premium is the modal
outcome of a long option, and at a ~50% hit rate with 2:1 payoff quarter-Kelly
is ~6%. 10% is already the aggressive end. The 30% aggregate cap is unchanged.

**"Competition capital" → account value throughout.** Every §3 percentage now
anchors on the full broker balance rather than on account value − $900. The
reserve stops being a scoring artifact and becomes a working settlement
buffer, enforced directly by the playbook's reserve invariant (total cash ≥
$900.00 at all times) rather than by subtraction from a capital definition.
Consequence stated plainly: every **equity** ceiling is nominally larger in
dollars than before, while the options sleeve moved the other way and moved
further.

**§3.6 migration note.** The circuit breaker is retained at −20% but now
measures account value against an account-value high-water mark. Every
`High-water mark:` figure already written to `status/` is a competition-capital
number, i.e. $900 low. Because the ratchet takes `max(prior, current)`, the
first session close after this amendment raises it and it is correct from then
on; for that single session the halt threshold sits $720 low, which makes the
brake harder to trip, never easier.

**Deliberately NOT changed.**

- **§1 in full** — no margin, no selling options, no spreads, no shorting, no
  penny/OTC. These are what make the system safe to run unattended and none of
  them constrains the strategy.
- **§3.3 expiration handling**, which now binds *harder*: removing the earnings
  bar means the account will hold long options through binary events on
  purpose, which makes finishing deep ITM more likely — and that is the exact
  input to OCC auto-exercise on a cash account. The 5-DTE close is the only
  thing between a correct thesis and a cash call.
- **§5 settlement, §7 logging, §9 amendment procedure.**
- **§1.7 transfers**, reworded only to drop its dead "during the window" scope
  and scoring justification. It is retained at full force and deliberately not
  relaxed: a §3.6 high-water mark means nothing if the denominator can be
  topped up. Relaxing it is a §9 conversation Chris can open at any time.
- **The playbook's rejected "catch-up branch"** (2026-08-13, no late-variance
  rotation to make up ground). Argued as final for the competition window; the
  window is gone but the ruling is kept, because its reasoning was about
  behaviour under pressure rather than about a deadline, and §9.3 forbids
  loosening a rule at the moment it would help.

**Enforcement added.** `scripts/check-consistency.sh` gained two checks: the
delta band must be ordered (min < max) and the strategy tightening must sit
*inside* it — the pre-existing tightness check compares only against the floor
and would have passed a strategy delta of 0.90, outside the band entirely.
It also now fails if any deleted competition or endgame-calendar key reappears
in `rules.yml`. `scripts/test-pre-order-check.sh` gained matching assertions,
including a functional one proving the gate rejects a 200.00 premium that the
old 20% cap admitted.

**Known follow-up, not done here.** The `Competition capital:` field in
`status/` files is a serialization key read by `scripts/status-write.sh`,
`scripts/scheduled-run.sh` and three test suites, and present in every
historical status file. Renaming it is a data-format migration and was
deliberately kept out of a rules amendment. It is now a legacy field name, not
a rule anchor.

### 2026-08-17 · §7 logging: trade log, status, and research are local-only

The trade log, `status/`, and `research/` were removed from git tracking and
purged from history ahead of publication. §7.1 rewritten, §7.2 marked
local-only, §7.3 extended, §7.4 added (the repository is public; nothing
sensitive may be committed).

**Git is no longer the audit trail for order flow.** The commit history still
timestamps the rules and every amendment; it no longer timestamps a trade. The
log is append-only by convention, on one machine, with no external witness —
a real weakening of the original design, accepted deliberately in exchange for
not publishing order flow.

### 2026-08-17 · §3.2 option caps raised

| | Before | After |
|---|---|---|
| Per position | 15% of competition capital (≈$435) | **20%** (≈$580) |
| Total open premium | 20% (≈$580) | **30%** (≈$870) |

Chris-initiated post-close, book flat. All quality floors, §3.3 expiration
handling, §3.7, and the 9/4 all-flat strategy rule unchanged.

### 2026-08-14 · Capital amendment: +$2,000.00 (mid-window, field-agreed)

Transferred in after the day-one close and verified settled at the broker:
$3,799.38 liquidation, $3,402.48 settled cash, $0 unsettled. Competition
capital ≈ **$2,899.38** at amendment.

Every competitor made the same addition, so the scoring formula was left
unchanged and rankings stay comparable. The §1.7 transfer prohibition was
ratified as a **one-time exception for this transfer only** and remains in
force for the rest of the window.

High-water mark re-anchored $900.00 → **$2,900.00** — a round number chosen by
Chris, absorbing day one's −$0.62 into the baseline. Circuit breaker moved with
it: Caution $792.00 → **$2,552.00**, Halt $720.00 → **$2,320.00**.

### 2026-08-13 · Pre-window amendments (field-agreed, before the first session)

| Rule | Change |
|---|---|
| Capital & scoring | Split into $900.00 competition capital + $900.00 settlement reserve. Score = final account value **minus the reserve**. |
| §3.2 | Option caps became percentages of competition capital, scaling with the account. The fixed $135/$180 caps and the $360 non-replenishing monthly cumulative cap were deleted; the two-position limit was removed. |
| §3.4 | Stop trigger declared a **floor** — may be raised, never lowered. Enables the playbook's ratchet. |
| §3.5, §3.8 | Re-anchored from "of account" to "of competition capital" — the old wording was 2× looser on the $1,800 balance than the header intended. |
| §3.7 | Scope ruled by Chris: binds a position to **its own underlying's** report. Third-party events are ordinary market risk. |
| §4.10 | The 5-orders-per-session ceiling lifted; per-symbol and per-stop ceilings stand. Ceilings trip on the attempt to exceed (N+1), ruled by Chris. |
| §4.10 | Protective-order exception ratified: a mandatory §3.4 stop is placed even if it breaches a ceiling, because the alternative is an unstopped position held overnight. |
| §2 | Long puts permitted; **all** option selling prohibited. The original "no puts" rule was written believing puts carry unlimited risk — they do not. The unlimited position is the uncovered short call. |

### 2026-08-17 · Options: delta floor 0.50 → 0.40, DTE floor derived (21 → 18)

Chris-initiated, from a review of the options rules. The prompting question was
why options carry "a requirement to hold for a certain number of days" — they
do not. Nothing in the manual constrains holding period. §3.2's DTE floor is a
*contract-selection* rule at entry and §3.3's is a *forced exit*; together they
describe a usable window, not a minimum hold.

**Δ floor 0.50 → 0.40** *(strategy rule, no §9)*. The manual's §3.2 floor of
0.35 sits exactly on the boundary of the documented Δ 0.05–0.35
lottery-overpricing zone, so a strategy buffer above it is warranted — but
0.50 was buying far more intrinsic value than the evidence asks for. Combined
with the §3.2 premium cap it forced deep, expensive contracts: few of them, and
low convexity for a book whose theses are directional and short-horizon.

**DTE floor 21 → 18, and now derived rather than chosen** *(§9)*. The floor
exists because long options get **no resting stop** (§3.4 excludes them), so a
position can only be closed by a live session — while §3.3 auto-exercise is
the largest event risk in the account. It is therefore not an options-theory
number but an operating-limitation one:

```
option_min_dte = option_close_at_dte      (5)   §3.3 forced exit
               + option_max_blind_days    (10)  7-day token lapse + long weekend
               + execution_margin_days    (3)   a close may need several sessions
               = 18
```

15 would be the bare minimum, but a worst-case blackout starting at 15 DTE
returns exactly at the §3.3 deadline with no room to trade. The 3-day execution
margin is what makes 18 rather than 15 the answer. The old flat 21 carried the
same protection with three days of unexamined margin on top.

`check-consistency.sh` now enforces the identity, so shortening the §3.3 exit
or lengthening the token cycle moves the floor instead of silently leaving it
stale — verified by setting `option_close_at_dte: 7` and confirming the build
fails.

### 2026-08-17 · Restriction audit: six rules loosened, one confirmed

Chris-initiated audit — "the rules are too strict and they restrict our ability
to actually find investable assets." Account at a high; §9.3 satisfied.

The audit's finding was that the premise was half right. The **core** universe
was not rule-starved: the deep-research design already records the naive band
as "300–500 names — unbuildable at ~5 spare chain calls/day", so the binding
constraint there is research throughput, not rule strictness. The **catalyst**
sleeve and the stop geometry were genuinely over-tight.

| # | Change | Gate |
|---|---|---|
| 1 | Catalyst price band `$20–60` → **at least 6 whole shares at sleeve size** (ceiling = sleeve ÷ 6, and it scales) | strategy rule |
| 2 | §3.4 stop: fixed 10% → **clamp(2.5 × daily ATR%, 8, 15)**; volatility ceiling re-keyed from "10% ÷ ATR% < 3" (ATR% > 3.33) to **ATR% > 6** | §9 |
| 3 | Core sector carve-out (outside consumer-retail and mega-cap tech) **removed** — its rationale was the 8/14–17 blind deployment, which has passed | strategy rule |
| 4 | NVDA guard: blanket 8/24–8/28 freeze → **8/25–8/27, correlated names only** (§3.8 >0.7); uncorrelated names stay tradeable | strategy rule |
| 5 | §1.4 liquidity: ≥1M shares/day → **≥$5M/day dollar volume** (plus a ≥100k share sanity floor) | §9 |
| 6 | §4.10 per-symbol ceiling **2 → 4** | §9 |
| 7 | §1.3 no spreads — **confirmed, no change** | — |

**On #2, the tradeoff stated plainly.** An ATR-scaled stop raises worst-case
loss per position from 10% to as much as 15%, but holds *risk per trade*
roughly constant instead of letting it vary inversely with volatility, and
removes the filter that was rejecting quality names purely because a fixed
10% stop did not fit them. The cap is what bounds the downside.

**On #5**, the old floor was a size-dependent proxy: at this account's maximum
§3.1 position, a $5M/day name absorbs us at roughly 0.02% of its volume.

**On #6**, an entry fill plus its mandatory §3.4 stop consumed the entire
2-order allowance, so any stop retry or entry re-price breached by
construction and forced the protective-order exception on routine operations.

**On #7 — §1.3 confirmed and its basis corrected.** The manual said Schwab
"places spreads in an approval tier that requires a margin account." Verified:
spreads are Level 2, and *"securities regulations require options spreads to
be traded in a margin account. Therefore, Levels 2 and 3 must have margin
enabled."* §1.1 is unamendable core, so §1.3 is downstream of a rule that
cannot move — not a discretionary tightening. Text updated to say so.

**Correction to an earlier claim in the same conversation.** The agent stated
that §1.2 was moot because the broker blocks option selling at Level 1. That
was wrong: Schwab Level 0 (Covered) carries covered calls, cash-secured puts
and collars, and Level 1 (Long) includes Level 0. The binding constraint on
option selling is **§1.2 itself**, which is unamendable core, not the broker.

### 2026-08-17 · §9.1 — the "no amendment proposed while a position is open" bar removed

Chris-initiated, post-close, account at a high (competition capital $2,902.00
vs HWM $2,900.00). §9.1 is not in the unamendable core.

**Removed:** "never proposed by me while a position is open."

**Why it went.** The bar was over-broad in practice. A strategy that is
usually holding something makes the manual effectively unimprovable for the
entire window — the clause blocked *raising* a rule, not merely amending one,
so any defect noticed mid-window had to wait for a flat book. It fired twice
on 2026-08-17 alone, once against a rule-quality audit Chris had explicitly
asked for.

**What replaces it.** Nothing, deliberately — §9.3 already targets the real
hazard. "Never as a reaction to a losing position, open or just closed"
blocks amendment under loss pressure, which is the danger; "a position
exists" was only ever a proxy for it, and a coarse one. §9.3 is now marked as
the load-bearing guard, with an explicit note that a rule I have just argued
would have benefited a current position is the clearest §9.3 breach rather
than an edge case.

**The tension, recorded rather than smoothed over.** This bar was removed
while two positions were open (USB, CSX), immediately after the agent
produced a list of six rules it judged too restrictive. That is precisely the
pattern the clause was written to prevent, and the record should say so. What
makes it defensible: Chris initiated it, the account was at a high rather
than in drawdown, §9.2 and §9.3 still gate every actual change, and the
unamendable core (§1.1, §1.2, §3.1, §3.6) is untouched.

### 2026-08-17 · Regression suite for the pre-order gate

`scripts/test-pre-order-check.sh` — 72 tests over the mandatory §4.9/§4.10
arithmetic gate, which until now had none. That absence is why the §3.2 cap
drift survived four days.

Covers every exit code (0 pass, 2 usage, 3 §1.4 floor, 4 §4.10 notional,
5 caps, 6 §5 settled cash, 7 rules-load failure); both sides of every cap
boundary to the cent; gate precedence; that percentage caps are *floored* and
never rounded up across a limit; that `rules.yml` genuinely drives the caps;
and that a missing, incomplete, or malformed `rules.yml` fails **closed** at
exit 7 rather than reading as a pass.

**Validated by mutation testing** — eight deliberate bugs injected into a copy
of the gate, each confirmed to fail the suite, with an unmutated control
passing clean. Among them a replay of the original bug (§3.2 single cap back
to 15%), a cap that rounds up instead of flooring, a §1.4 off-by-one, and an
inverted settled-cash check. A suite that survives its own mutations proves
nothing; these do not.

Two defects found while writing it:

- `pre-order-check.sh` relied on `BASH_SOURCE` to locate its rule library, so
  running it under a non-bash shell resolved the library against the caller's
  cwd. It failed closed (exit 7) but reported "command not found: load_rules".
  It now refuses plainly with exit 2 if `BASH_VERSION` is unset.
- The first draft of the suite invoked the gate through `$SHELL` rather than
  `bash`, bypassing the shebang — the test bug that surfaced the one above.

### 2026-08-17 · rules.yml: one home for every rule parameter

Restructure by **mutability** rather than topic, after the day's second
parameter-drift bug.

**`rules.yml` (new)** — every rule number, once. Two sections: `manual:`
(§9-gated) and `strategy:` (*(strategy rule)*, discretionary). Amending a rule
now means editing `rules.yml` and `CLAUDE.md` in the same commit.

**`scripts/lib-rules.sh` (new)** — dependency-free awk loader. Fails loudly: a
missing file or absent key returns non-zero rather than defaulting, because a
defaulted cap is a cap that silently stops binding.

**`scripts/pre-order-check.sh`** — reads its §3.1/§3.2/§3.5 caps and the §1.4
floor from `rules.yml` instead of hard-coding them. New exit code **7** for a
rules-load failure, kept distinct from 6 (insufficient settled cash) so a
broken rules file can never be misread as a cash result. Verified fail-closed:
with `rules.yml` absent the gate exits 7 and evaluates nothing.

**`scripts/check-consistency.sh` (new)** — three checks. (1) Every
`**N**<!--rule:key-->` annotation in a doc matches `rules.yml`; 27 annotations
across `CLAUDE.md` and `strategy.md`. (2) Every strategy value that shadows a
manual value is on the stricter side. (3) No script hard-codes a rule
percentage. Wired into §4.5 session-open and the playbook §9 open sequence.
All three detections were tested by deliberately introducing each drift.

**`strategy.md`** — the playbook moved from
`docs/superpowers/specs/2026-08-13-competition-strategy-design.md` to the
repository root. It was described as "required reading at every session open"
while living at a dated spec path. It now cites manual sections instead of
restating their numbers.

**`CLAUDE.md` keeps its name** — it auto-loads at session start by filename
convention. Renaming it to `guardrails.md` would have stopped the binding rule
set from loading automatically, which is the failure the manual warns about
for the playbook. It is the guardrails file; it is named for the loader.

### 2026-08-17 · §3.6 Caution removed; Halt is the only drawdown level

Chris-initiated, post-close, market closed, no option position open, and the
account at a **new high** — competition capital $2,902.00 against a recorded
HWM of $2,900.00, +0.07%. §9.1 and §9.3 are satisfied on the strictest
reading: this is not a reaction to a losing position, open or just closed.

§3.6 is named in §9's **unamendable core**, so the removal was confirmed
explicitly rather than inferred. Chris's first instruction was "Nothing should
happen at caution. In fact, remove caution and halt"; asked how far to go, he
chose to neutralize Caution and **keep Halt as-is**. §3.6 therefore survives,
and stays in the unamendable core.

**Removed.** The Caution band at −12% (0.88 × HWM), which halved all new
position sizes, blocked new option positions, and closed open option
positions. Its option clause was the ambiguity flagged earlier the same day —
"close any option position at a loss" read either as *only the losing ones* or
*all of them, accepting losses*, and the playbook had pinned the second while
contradicting itself in the same sentence. Removing the band resolves the
ambiguity by deletion.

**Unchanged.** Halt at −20% (0.80 × HWM): no buy orders of any kind, notify
Chris, resume only after an explicit conversation. §3.5's forced close and all
closing orders still work at any drawdown.

Propagated to the playbook (§3 thresholds, the §8 monitoring table), tick.md
(§B5 compute, watch 3, the ledger `LEVEL` field — now `OK`/`HALT`),
`research.md` and the research-loop design (the ping gate's drawdown check is
now structurally vacuous, since Halt stops the loop upstream), and the README.
§3.6 carries an explicit "there is no intermediate level" clause so a future
session cannot re-infer one.

### 2026-08-17 · Cash figures removed from the rules; contradictions swept

Caps and thresholds are now stated only as percentages and formulas. Dollar
equivalents are resolved at the moment they are needed — competition capital
from the live broker read, the high-water mark from the latest `status/` file,
cap arithmetic by `scripts/pre-order-check.sh`. The $900.00 reserve and the
§1.4 $5.00 floor are the standing exceptions, both fixed by definition.

Removed from the manual: the hard-coded high-water mark, Caution and Halt
dollar levels, the competition-capital snapshot, and the §3.2 dollar caps.
§3.6 now states `≤ 0.88 × HWM` / `≤ 0.80 × HWM`.

**Live bug found and fixed:** `scripts/pre-order-check.sh` still enforced the
pre-2026-08-17 option caps — 15% per position and 20% aggregate, against the
amended 20% / 30%. The §3.2 aggregate and the §3.5 leveraged aggregate were
both 20% before the amendment and *shared one variable*, so raising §3.2 to
30% silently missed the script. Caps are now one variable per rule
(`cap_pos_e4`, `cap_opt_single_e4`, `cap_opt_agg_e4`, `cap_lev_e4`). The gate
had been rejecting compliant option orders — the safe direction, and no option
position was open while it was wrong.

Contradictions fixed: §3.6 and §4.5 both claimed to be "the first action of
every session" (§4.5 is; §3.6 follows it); tick.md §E cited the §4.10
*replace* ceiling to license 3 stop *placement* attempts, when a
never-rested stop is a new order against the 2-per-symbol ceiling and
attempts past the first are protective-order-exception breaches; the playbook
offered leveraged ETFs for the §10 catch-up branch that §10 records as
rejected outright; the playbook's day-4 leveraged exit was stricter than the
manual's day 5 but unmarked as a *(strategy rule)*.

---

## Playbook — `strategy.md`

| Date | Change |
|---|---|
| 2026-08-13 | **Revision 2** — four-agent adversarial review incorporated. Stop ratchet adopted; §3.7 read as own-earnings only; the 9/8–9/9 catch-up branch rejected outright by Chris in calm conditions. |
| 2026-08-13 | **Revision 3** — five-agent comparative research adopted: Δ≥0.50 for long premium, post-gap ATR for catalyst entries, unspent-as-default for the options sleeve. |
| 2026-08-13 | Options sleeve degated — the piggyback/anti-field entry gating was a strategy rule and was removed entirely. |
| 2026-08-14 | Sleeve ceilings in dollars moved with the capital amendment: core $450→$1,450, catalyst $270→$870, leveraged $180→$580, max deployed $900→$2,899.38. Percentages unchanged. |
| 2026-08-14 | Research loop added (`/research`, `research-scout`). Options additionally clear a ladder/IV assessment before HOT; the post-close pass takes a daily OI-delta snapshot. |
| 2026-08-17 | Core sleeve name count removed (was "2 names, ~25% each"); per-position §3.1/§3.8 bind instead. |
| 2026-08-17 | All-options-flat date moved 9/9 → **9/4**, off the far side of the Labor Day dark stretch. |
| 2026-08-17 | §6 rewritten. It had drifted to a stale $435 per-position cap and a wrong "20% open / $580" figure; both corrected against manual §3.2 (20%/$580 per position, 30%/$870 open). Duplicated lines removed. |
| 2026-08-19 | **Pre-open brief given a catch-up path** (§9 Open). The `/deep-research preopen` cron is session-scoped and fires at 05:15 PT, so any session opening later structurally could not produce its own brief — four consecutive misses, 8/15–8/19, each logged as an incident before the pattern was read. Session open now runs the brief itself when today's file is absent and it is before 12:00 ET; after noon it is skipped and reported, because the live `/research` loop has already covered the tape on RTH data by then. Cron re-creation was never a fix for this — it prevents tomorrow's miss, not today's. Mechanics in `.claude/commands/deep-research.md` §A.6 (idempotency), §P (catch-up stamp, prohibitions carried), §Dispatch. No rule value moved; no manual change; `rules.yml` untouched. |
| 2026-08-20 | **Catalyst minimum share count 6 → 3**, lifting the price ceiling from `sleeve ÷ 6` (~$144.68) to `sleeve ÷ 3` (~$289.37) at present capital. The ceiling is a granularity rule, but at 6 shares it was deciding *which sleeve a thesis belonged to*: TGT and HD both failed it on price while independently passing §4 core selection, so the same post-earnings trade was blocked as catalyst yet available as core — with core's slower exits and no stall rule. Options put to Chris were (a) route such names to core carrying §5 exits, (b) core plainly, (c) block cross-sleeve routing outright, (d) fix the root cause. Chris: "Fix the root cause — lower the share minimum". Accepted cost: a 3-share position scales coarsely — partial exits move in thirds — so on those positions the ratchet does the work scaling out would otherwise do. `rules.yml` `strategy_catalyst_min_whole_shares` and §5 amended together; 72/72 pre-order tests pass, consistency exit 0. |
| 2026-08-24 | **§0 Operating limitation #1 amended — "I am not continuous" → "I am scheduled, not continuous."** Prior text, now superseded: *"I am not continuous. I act only while a session is open on this machine. I am not watching the market between sessions, overnight, or on weekends."* That statement became **false** when the scheduled jobs moved to an always-on server, and a false limitation is more dangerous than a strict one precisely because it is relied upon — it would have sat in the manual describing a machine that no longer existed. Chris initiated this (§9.1) by choosing "Full continuous operation now" and then "do the ammentment and merge"; quoted verbatim in the commit body per §9.2. Not a reaction to a losing position (§9.3) — no position was open or recently closed in connection with it, and it loosens no risk rule. **Nothing in the unamendable core is touched:** §1.1 no margin, §1.2 no selling options, §3.1 position cap and §3.6 circuit breaker all stand unchanged, as do §1–§5 entire. The replacement authorises unattended scheduled operation and is deliberately explicit about what that still is **not**: scheduled means gaps by construction (a move beginning and completing between two ticks is unseen); nothing watches overnight, weekends, or pre/post-market, where only resting GTC stops are live; the machinery can be down and being down is *silent from the inside*, so a stopped container or lapsed token means no action rather than degraded action; and unattended is not unapproved — the Discord approval gate still stands in front of every order, and §6's "if I am uncertain, the answer is no" is unchanged. Limitation #2 (stops do not cover gaps) is untouched and now carries more weight, not less. The section closes on the reading it exists to enforce: **absence of action is never evidence that nothing needed doing.** |

---

## Repository

| Date | Change |
|---|---|
| 2026-08-13 | History rewritten before first publication to remove the brokerage account number, the Schwab API `accountHash`, a reference to unrelated personal holdings, and the author's email. No dollar figure, trade, rule, rationale, or timestamp altered. |
| 2026-08-17 | `trade-log.csv`, `status/`, and `research/` purged from history and gitignored. History squashed to a single commit. Repository made public, then deleted and recreated to clear orphaned objects left reachable by SHA. |
| 2026-08-14 | Tick cadence baseline lowered 5 min → **15 min** at the close review (Chris: "Lower tick latency"). Day one ran 72 five-minute rows, 0 trips, ~2.4M subagent tokens. |
| 2026-08-17 | Simplification pass. Five completed documents moved to `docs/archive/` unedited. Manual and playbook history moved here. |
| 2026-08-24 | **Scheduled jobs moved off the session onto an always-on server** (Docker Compose, Ubuntu). The harness `CronCreate` entries the deep-research loop depended on are in-memory and die with the session, and the machine running them was a laptop that slept — so the 05:15 PT pre-open job could not fire on a closed lid at all. `research/preopen/` had lost 8/19 and 8/24 on top of the 8/15–8/19 run already recorded above; `CronList` returned "No scheduled jobs" at the session that found this. New: `docker/` (pinned image — claude 2.1.234, schwab-mcp at git `5be6357`, supercronic v0.2.49 checksummed; compose topology; ET crontab), `scripts/scheduled-run.sh` (one entry point owning lock, ET window guard, heartbeat, Discord relay), `scripts/repo-update.sh`, `scripts/deploy.sh`, `scripts/bootstrap-server.sh`, `scripts/token-watchdog.sh`, `scripts/job-deadman.sh`, `scripts/discord-notify.sh`, `scripts/sidecar-sync.sh`, `scripts/test-scheduled-run.sh` (21 guard tests, mutation-verified), `scripts/test-broker-readonly.sh` (7 tests run against the built image), `docker/broker-entrypoint.sh`, `.github/workflows/image.yml`. The broker runs as ONE long-lived `--http` service rather than a stdio subprocess per run, so there is one token holder and one re-auth point instead of N processes racing on `token.yaml`, each able to trigger a blocking 5-minute login flow. It is started with neither `--jesus-take-the-wheel` nor the Discord env vars, so `cli.py` sets `allow_write=False`. Measured in the built image rather than inferred: that registers 23 tools instead of 25, and the two withheld are exactly `place_previewed_order` and `cancel_order` — the scheduled container has no route to sending or cancelling an order, below and independent of the agent toolset. The seven `preview_*_order` tools remain registered in both modes; a preview is a real API call that changes nothing and cannot become an order without `place_previewed_order`. An earlier draft of this entry claimed the order tools were absent entirely, which the empirical check disproved.

Two further findings came only from building and running the image, not from reading the source. **(a)** Starting the broker with no token does not fail fast: `easy_client` falls through to `client_from_login_flow`, which blocks 300s waiting for a browser callback no container can supply, then exits — under `restart: unless-stopped` that is a five-minute-per-iteration crash-loop hammering Schwab's auth endpoint and emitting one alert per restart. `docker/broker-entrypoint.sh` now checks token presence and age first, waits quietly instead, notifies once, and self-starts within one poll of a fresh token appearing, so re-auth needs no restart. **(b)** That entrypoint needs Discord to report the stall — but `discord_requested` is `any(token, channel, approvers)` read from `SCHWAB_MCP_DISCORD_*`, so giving the broker those names purely to send a message would have silently set `allow_write=True` and handed it `place_previewed_order`. The broker therefore carries the same credentials as `TC_DISCORD_*`, which schwab-mcp never reads; `test-broker-readonly.sh` asserts the canonical names never reappear on that service. `check-consistency.sh` gains check 4 (the ungated-broker flag may not appear anywhere) and check 5 (crontab times must match the times the command files document); both mutation-tested. `lib-lock.sh` gains an opt-in stale-lock breaker, default off, so existing callers are unchanged. `.gitignore` gains `!.env.example` — the `.env.*` rule was silently swallowing the template that `bootstrap-server.sh` needs to exist on a fresh clone. Playbook §9 and both command files updated; the §9 pre-open catch-up **stays** and now covers a downed container rather than a late session. No rule value moved; `rules.yml` untouched; 72/72 pre-order tests pass. |
| 2026-08-24 | **Working data moved to a private sidecar repo** (`Kilowhisky/trade-challenge-store`, verified private before anything was pushed). `research/`, `status/` and `trade-log.csv` now live there; the public repo holds symlinks, so every path the manual, playbook, command files and scripts name works unchanged. This is the delivery path for scheduled-job output from the server — a brief written at 08:17 ET on an unattended box has to reach Chris somehow — and it restores the external witness §7.1 records as lost ("append-only by convention, on one machine, with no external witness"). Import verified: `trade-log.csv` byte-identical to its pre-move digest, 36 research + 37 status files intact, no credentials, and every account identifier already carrying its `ACCOUNT_REDACTED`/`HASH_REDACTED` placeholder. **A bug this surfaced:** those paths were ignored as `research/` and `status/` — a trailing slash matches a *directory only*, and a symlink is not a directory to git, so the instant they became symlinks they fell out of the ignore rules and appeared as untracked, one `git add -A` away from committing machine-specific absolute paths into a public repo. Slashes dropped; `check-consistency.sh` check 6 now asserts all three stay ignored *and* untracked, mutation-tested against the exact regression. **A second bug, self-inflicted:** `scheduled-run.sh` called `sidecar-sync.sh` unconditionally, so the regression suite — which drives every window-guard case through that script — committed and pushed a heartbeat to the live private repo for each case that passed its guard, seven junk commits before it was noticed. Dry run now skips the sync entirely, verified by asserting the suite leaves the store's commit count unchanged. The junk was removed **forward, not force-pushed**: a repo whose purpose is an append-only external witness does not get its history rewritten on day one to hide an inconvenient hour. |
| 2026-08-24 | **Server identified and host-tuned: it is a Raspberry Pi 4, not an x86 box.** `brewmaster` is a Pi 4 Model B (4 cores, 3.7Gi RAM, Debian 12, rooted on an SD card) — **aarch64**, which the migration had assumed was amd64. Consequences: the Dockerfile's supercronic pin was `linux-amd64` only and is now arch-aware with a verified checksum per architecture; `.github/workflows/image.yml` now builds `linux/amd64,linux/arm64` and **fails CI if the pushed manifest lacks arm64** — otherwise the first `deploy.sh` run finds out with an `exec format error` at 18:30 ET. The box is also **not dedicated**: it serves `pihole-FTL` (DNS for the whole network) and Home Assistant (~470MB, the largest single process on it). So containers get `mem_limit` (broker 512m, scheduler 2g) and a positive `oom_score_adj` (500/800), making our jobs the preferred OOM casualty rather than the household's DNS; `scheduled-run.sh` now distinguishes exit 137 (SIGKILL, usually OOM on this box) from 124 (timeout), because reporting an OOM as a timeout sends you hunting a slow API. Host memory tuned by `scripts/setup-host-memory.sh` (new, idempotent): **zram 1.5G zstd at priority 100** as tier 1 and the SD swapfile raised 512M→2G at priority −2 as a deep backstop, with `vm.swappiness=100`. High swappiness is deliberate and inverts the usual advice, which assumes swap means a disk: with zram as tier 1, swapping early into fast compressed RAM is what keeps the OOM killer away from Pi-hole, while SD swap — slow enough that a thrashing Pi is effectively down, and a source of flash wear — is reached only in extremis. NTP verified active and synchronized first, since a Pi has no RTC and a scheduler with a drifting clock is worse than none. |
| 2026-08-24 | **CI hardened and a silent rollback failure fixed, both found before either ran.** `deploy.sh` recorded `docker inspect --format '{{.Id}}'` — a *local image id* — as its rollback target, and compose interpolated it as a tag, producing `ghcr.io/…:sha256:0f8b…`. Docker rejects that as an invalid reference, so the rollback did nothing while the Discord message announced a successful restore. **A rollback that lies is worse than none, because it is believed at the one moment something is already broken.** Now: the previous image is captured as a registry digest (`RepoDigests[0]`), compose takes a single full `TC_IMAGE` reference instead of a `:${IMAGE_TAG}` suffix so a digest is expressible at all, the recorded target is validated against an image-reference pattern before use, and a failed rollback reports failure rather than success. `scripts/test-deploy.sh` (new, 9 tests) pins it, including refusing the exact malformed value that caused the bug. In the workflow: the `verify pins` step built `ghcr.io/Kilowhisky/…` from `github.repository` while `metadata-action` lowercases only the pushed tag — docker rejects uppercase repository names, so that step would have failed on its first line every run; it now lowercases and addresses the image by `steps.build.outputs.digest` rather than reconstructing a tag from `git rev-parse --short`, whose width is `core.abbrev=auto` and can drift from metadata-action's fixed 7. Pins are now verified on **both** architectures rather than only amd64, the one that never runs in production. All six actions were a full major behind on the node20 runtime, which GitHub removes around 2026-09-16 — inside the competition window — and are bumped to checkout@v7, setup-qemu@v4, setup-buildx@v4, login@v4, metadata@v6, build-push@v7, verified against the GitHub API rather than from memory. QEMU now registers before buildx (the builder detects servable platforms at startup). A `v*` tag build would have produced an image the Pi never pulls, because `{{is_default_branch}}` is false for any tag ref while `deploy.sh` resolves `:latest` only; `latest` is now also applied on `v*` tags. Added `concurrency` (two builds racing to own `:latest` feed straight into the 18:30 auto-deploy) and a 90-minute timeout. `actionlint` clean. |
| 2026-08-25 | **The server broker moved from read-only to Discord-gated writes (`allow_write` state 1 → state 2), and the Mac now trades through it instead of holding a token of its own.** Root cause: Schwab issues **one refresh token per app authorisation**, so the 02:18 ET server re-auth silently revoked the laptop's token. The laptop read `invalid_grant` at 08:46, diagnosed "the token expired", raised `ALERT.md` and went closing-only — while `tc-broker` read the account cleanly all day (preopen 08:17, postclose 16:22). Session 8 therefore ran with **no §4.5 reconciliation, no §3.6 check and zero ticks**; the three GTC stops were the only protection, and the day's 11:14 catch-up brief opened by asserting a cron had not fired that had in fact succeeded three hours earlier. Nothing was traded and marks moved −$7.00, which is luck rather than design. Two installs cannot both hold a live token, so there is now one broker — and a single broker that cannot place also **cannot close**, which is why read-only stopped being the safer state. `broker` therefore gets all three `SCHWAB_MCP_DISCORD_*` names, `APPROVERS` explicitly, since token+channel alone would gate on a reaction from anyone who can see the channel; every write still blocks on Chris's ✅/❌ exactly as *Verified account facts* describes. **No §9 amendment: the 2026-08-24 §0 amendment already authorises unattended operation with the gate intact** ("Unattended is not unapproved. Every order still passes the Discord approval gate"), and `--jesus-take-the-wheel` — the mode that removes the human — stays banned repo-wide by `check-consistency.sh`. `test-broker-readonly.sh` renamed **`test-broker-gating.sh`** (a file asserting the opposite of its name is a defect) and its compose tripwire inverted: all three names must be present, and the partial case — writes enabled, approver list incomplete — fails loudly on its own rather than reading as a near-miss. The guard the old broker used to provide implicitly now has a test: `scheduled-run.sh` still passes jobs read-only Schwab tools, and `test-scheduled-run.sh` fails if `place_previewed_order` or `cancel_order` ever appears in one of their allowlists (25 tests). That test was **mutation-verified, and its first draft failed the mutation** — one clock for all three jobs meant two of them skipped their ET window, dispatched nothing, and the check passed on an empty string; each job now gets a clock and day inside its own window, and the loop fails if any job does not dispatch. Also fixed, both found in that same run's own logs: `python3` was absent from the image, so `universe-filter.sh` could not run and postclose skipped universe-screen qualification entirely; and nothing ever sourced `.env.local`, so `FMP_API_KEY` was unreadable and the drift screen fell back to a weaker web sweep it correctly labelled as weaker. `broker` is now published on the host loopback (`127.0.0.1:8000`) so the Mac reaches it over `ssh -L` without depending on a bridge IP that changes on recreate. No rule value moved; `rules.yml` untouched. **Deployed the same evening, and deploying is what found the next defect: `deploy.sh`'s post-deploy smoke test could never pass.** It put its prompt after `--allowedTools`, which is variadic, so claude parsed the prompt as one more tool name and exited with *"Input must be provided either through stdin or as a prompt argument"* — output `deploy.sh` discards, scoring it as an unhealthy deploy. Every deploy that actually reached a new image therefore failed its health check and rolled back: a pipeline structurally incapable of shipping, whose symptom pointed at the image rather than at the test. It survived because the nightly run almost always takes the no-new-image path and never reaches the smoke step; tonight's was the first to get there. This is the same defect `scheduled-run.sh` was fixed for, at a second call site nobody re-read — that one has had a guard ever since, this one had none. Now piped on stdin, verified end-to-end against the live broker, and pinned by two assertions in `test-deploy.sh`, mutation-tested against the exact original text. Also fixed there: the suite's synthesized env lacked `SCHWAB_MCP_DISCORD_APPROVERS`, which this entry's `:?` guard had just made required, so compose rendered nothing and the rollback-reference test failed on an empty string. |

---

## Pre-competition checklist (worked 2026-08-13)

Completed before the window opened. Its still-operative findings live in
`CLAUDE.md` under *Verified account facts*; this is the original record.

- [x] Confirm account type is **cash**, not margin — `type: CASH` on ACCOUNT_REDACTED.
      Entire margin field set absent (no `buyingPower`, `sma`, `marginBalance`,
      `regTCall`, `dayTradingBuyingPower`). `isClosingOnlyRestricted: false`.
- [x] Read the account's **actual options approval string** from Schwab and
      record it. Do not rely on tier numbers — broker numbering varies and
      differs from generic guides. Confirm specifically that **buying calls and
      puts** is permitted. Never request an upgrade requiring margin.
      *The MCP exposes no field carrying this string. Chris supplied it by eye
      2026-08-13, verbatim: **"level 1 options. 'Long'"**. Independently
      corroborated the same day — `preview_option_order` BUY_TO_OPEN 1x
      `F 260918C00014000` returned `status: ACCEPTED`, which a permissions
      failure would not. Long calls and puts permitted, consistent with §2, no
      margin required. If Schwab's UI shows a fuller string, replace the quote.*
- [x] Confirm starting balance is $900.00 and record it as the initial
      high-water mark — $900.00 confirmed, no positions, no open orders.
      **HWM $900.00** · Caution $792.00 · Halt $720.00.
- [~] Confirm the Schwab MCP connects, reads balances, and can place orders —
      **connects and reads: yes. Previews: yes, and validated live.** Order
      placement is locked off at the MCP as **Chris's own deliberate switch** —
      held off until the agent has earned execution authority, not an MCP
      defect. (An earlier status note diagnosed it as an unfixable tool-surface
      limitation; that was wrong and is corrected in `status/2026-08-13.md`.)
      **The switch flipped 2026-08-13 evening**, ahead of schedule: Discord
      approval mode is live and verified end-to-end (every write call blocks
      on Chris's ✅/❌ in `#llm-yolo`; details in the day-one run sheet and
      the `schwab-mcp-discord-approval` memory). `place_previewed_order` and
      `cancel_order` are **present** on the tool surface; **`replace_order`
      is absent** (see §4.10 counting). **Before the first real entry:
      re-verify both tools exist, then run the run sheet's two-order F
      drill.** Until that passes, treat execution as manual.
- [x] Verify settled vs. unsettled cash fields are readable (§5 depends on this)
      — `currentBalances.cashAvailableForTrading` (settled, gates every buy),
      `cashAvailableForWithdrawal`, `unsettledCash`, `cashCall`. Read
      `currentBalances`, never `initialBalances`.
- [x] Set START_DATE and END_DATE, and identify the final NYSE session —
      2026-08-14 → 2026-09-14. Final session Mon 2026-09-14. 21 sessions.
      §8 lockout begins Thu 2026-09-10.
- [x] Create `status/` directory
- [x] Commit initial state to git

**Known operating constraint (not yet an amendment).** Schwab refresh tokens
expire after 7 days and cannot be extended programmatically — 4–5 manual
re-auths across this window, each needing an interactive session from Chris. If a
token lapses I lose all read access: no §4.5 reconciliation, no §3.6 check, no
ability to close. Resting GTC stops still function; they live at Schwab. Also:
only account ACCOUNT_REDACTED is in the token's scope. Keep it that way on every
re-auth.
