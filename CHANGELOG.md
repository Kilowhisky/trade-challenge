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

---

## Repository

| Date | Change |
|---|---|
| 2026-08-13 | History rewritten before first publication to remove the brokerage account number, the Schwab API `accountHash`, a reference to unrelated personal holdings, and the author's email. No dollar figure, trade, rule, rationale, or timestamp altered. |
| 2026-08-17 | `trade-log.csv`, `status/`, and `research/` purged from history and gitignored. History squashed to a single commit. Repository made public, then deleted and recreated to clear orphaned objects left reachable by SHA. |
| 2026-08-14 | Tick cadence baseline lowered 5 min → **15 min** at the close review (Chris: "Lower tick latency"). Day one ran 72 five-minute rows, 0 trips, ~2.4M subagent tokens. |
| 2026-08-17 | Simplification pass. Five completed documents moved to `docs/archive/` unedited. Manual and playbook history moved here. |

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
