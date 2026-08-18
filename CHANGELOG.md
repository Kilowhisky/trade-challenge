# Changelog

History for `CLAUDE.md` (the operating manual) and
`docs/superpowers/specs/2026-08-13-competition-strategy-design.md` (the
playbook).

**This file is not read during a session.** It exists so those two documents
can state only what is currently true, without carrying their own history in
the context that loads every time. Read it when you need to know why a rule
says what it says, or what value it used to carry.

Manual changes go through §9: an explicit conversation Chris initiates,
recorded as a commit with his own message quoted verbatim, never while a
position is open and never in reaction to a loss. Playbook rules marked
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

## Playbook — `2026-08-13-competition-strategy-design.md`

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
