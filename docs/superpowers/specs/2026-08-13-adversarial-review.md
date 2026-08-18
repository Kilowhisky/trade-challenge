# Adversarial Review — Findings and Dispositions

**Date:** 2026-08-13. Four independent red-team agents attacked the strategy
spec at Chris's direction: competitive/game-theory, internal consistency,
operational failure modes, and the PEAD thesis itself. None saw the others'
work. This file records every finding that survived and what was done with it.
Full agent reports live in the session transcript; this is the audit summary.

**Convergent findings (found independently by ≥2 agents) — all accepted:**

| Finding | Found by | Disposition |
|---|---|---|
| 9/9 token re-auth collides with options deadline + post-holiday pileup | ops, consistency | **Fixed:** re-auth moved to 9/8 pre-market (token then outlives the 9/14 bell); calendar now 8/19, 8/26, 9/2, 9/8 |
| Option piggyback rule quietly re-bans puts / chains options to an empty set | competitive, thesis | **Fixed:** piggyback-only deleted; see spec §6. Anti-field put scope pending Chris ruling |
| Deadline stack after Labor Day dark stretch | ops, consistency | **Fixed:** all discretionary clocks pulled to 9/4 — options flat 9/4 close, last routine entries 9/4 |
| 10-session time exit clips winners; drift horizon unreachable in-window | competitive, thesis | **Fixed:** time exit deleted; exits = stop + stall rule + ratchet + endgame calendar |
| Reserve/GFV architecture | ops (ledger walk), consistency (edge case) | **Held**, with two fixes: bridge invariant redefined (below); settled-cash field semantics gated until observed |

## Competitive agent — key findings and dispositions

1. **"Finishing well" pays $0 in winner-take-all; the design optimized rank,
   not P(first).** ACCEPTED. Spec §1 rewritten to state the true trade
   plainly: the manual's box costs roughly the competition (est. P(first)
   5–25%, top of range only because the field is correlated-long), and buys
   survival of real money. That is Chris's chosen trade; it is now stated
   rather than dressed up.
2. **Blow-ups don't help at small N** — the winner is the max order statistic;
   competitors cratering improves our rank, which pays nothing. ACCEPTED as
   framing. Corollary adopted: our only real win path is a correlated-long
   field (NVDA/AI complex) having a bad month while we preserve capital.
3. **NVDA 8/26 (confirmed) is the month's dominant event and we are
   structurally excluded from direct participation** — equity through the
   print banned (§3.7), compliant NVDA option contracts don't exist under the
   $135 cap, post-print position ≈ 1 share. ACCEPTED as a stated cost of the
   box, not relabeled a benefit. NVDA straddle has lost money 7 straight
   quarters — the competitor's calls are not a free win.
4. **No catch-up mechanism exists if behind late.** PARTIALLY ACCEPTED:
   a pre-authorized, Chris-activated max-beta compliant branch for 9/8–9/9
   drafted into spec §10, pending Chris (defined now, in calm conditions,
   precisely because §0 says the pressure peaks when down).
5. **Friction at $900 scale**: prefer catalyst names in the ~$20–60 price
   band; count expected stop slippage (realized stops skew −12 to −15%, not
   −10%) in the §4.9 check. ACCEPTED.

## Consistency agent — key findings and dispositions

1. **Bridge rule leaked the reserve** (stop-out at a loss + full-size
   redeploy → permanent reserve deficit; three rules gave three answers).
   ACCEPTED. Replaced with one invariant: **total cash (settled + unsettled)
   ≥ $900 at all times**; bridged buys capped at min(planned size, incoming
   unsettled proceeds); same-day proceeds qualify.
2. **Monitoring table hardcoded $792/$720; §3.6 ratchets with HWM.**
   ACCEPTED — real bug. Now 0.88 × HWM / 0.80 × HWM, HWM re-read each tick.
3. **Option-exit deadlock** (bid collapses 9/9; §4.1 forbids the market order
   §8 needs). ACCEPTED: options flat by 9/4 close with an escalating
   limit-reprice ladder ending marketable-at-the-bid; unfilled-at-bell branch
   defined in writing (marketable limit next open, logged per §7.3).
4. **Stop ratchet fails the "manual wins" test** — §3.4's literal trigger is
   10% below entry; the cited permission doesn't exist. ACCEPTED. Ratchet
   suspended pending Chris's §9 one-liner ("trigger is a floor; may be
   raised, never lowered") or dropped. Mechanics fixed regardless: amendments
   via single replace (never cancel-then-place), limit stays 5% below
   trigger, §4.6 applies to replaces.
5. **Sleeve percentages vs a marks-moving denominator.** ACCEPTED: sleeve
   compliance is checked at order time only; mark drift never forces sales
   and never frees option capacity; "open premium" = premium paid.
6. **"Wind-down" undefined; Caution clause ambiguous.** ACCEPTED: halved
   ceilings gate new buys only; existing positions are never force-sold by
   Caution; the option clause is pinned to "close all open options, accepting
   losses."
7. **§3.8 had no measurement method.** ACCEPTED: evaluated at entry (sector
   classification + 60-day daily-return correlation); mid-hold convergence is
   not a violation but blocks adds to the correlated cluster; weekly check
   added to the monitoring table.
8. **Every time clock was off-by-one; multi-lot entries undefined.**
   ACCEPTED: entry day = session 1; clocks run from first fill; entry price =
   share-weighted average; one stop at 10% below blended entry, re-computed
   via replace on adds; lockout language corrected to "from 9/10."
9. **The amended manual is internally inconsistent** — header anchors to
   competition capital; §3.1/§3.6 body still say "account value" (literal
   reading post-transfer: Caution at comp value $684). Spec now states the
   header controls; body propagation queued for Chris's §9 sign-off.

## Ops agent — key findings and dispositions

1. **Token/gap/holiday cliff (SEV-1):** a gapped stop-limit with no session
   and a dead token persists up to 4 sessions; ~2.3× designed loss ($89 vs
   $39 on a $270 position); §3.6 cannot fire while blind. ACCEPTED:
   re-auth 9/8; clocks to 9/4; token-death rule (if Chris cannot re-auth
   before ≥2 dark days, flatten or log the accepted gap risk in writing);
   market-exit procedure now cancels the consumed stop-limit first (closes
   an accidental-short path).
2. **Manual-exit ordering race (SEV-3):** sell-then-cancel leaves an orphaned
   GTC stop if the session dies between calls → cash-account short, §1.5
   event. ACCEPTED — highest-value one-line fix: **cancel the stop first,
   then sell.**
3. **Honest loop coverage: 5–15% of the trading day, ≈0% of open/close
   windows.** ACCEPTED and stated in the spec; operative deadline everywhere
   is now *session end*, not market close; entry orders may only be working
   while a session is alive to watch them; Chris asked to adopt a 5-minute
   9:30 ET check-in habit on any day a position is open.
4. **Dead-zone fill (SEV-4): mostly covered** by day-only entries + §4.5
   broker-first reconciliation. Fixes adopted: entries only in the first half
   of a session Chris intends to keep open; "order placed, verification
   pending" written to the log between place and verify.
5. **Reserve/GFV (SEV-5): held.** One untested assumption caught:
   `cashAvailableForTrading` has only been observed with unsettledCash = 0.
   Until its semantics are observed on the first real sale, every buy gates
   on `cashAvailableForTrading − unsettledCash` (wrong only in the safe
   direction).

## Thesis agent — key findings and dispositions

1. **Academic PEAD is dead in the liquid universe the manual forces us into**
   (Martineau; Subrahmanyam's demolition of the 2025 revival — t=1.43 ex
   microcaps). The window also cannot reach the day-20–75 horizon where
   practitioners claim residual drift. ACCEPTED: **thesis relabeled from
   "post-earnings drift" to "post-earnings momentum continuation."** What the
   qualification rules actually select — a high-turnover large cap gapping to
   near its 52-week high — has current, replicated support through the
   short-term momentum / 52-week-high channel (Medhat–Schmeling;
   Chen–Stivers–Sun 2024), which is strongest in exactly our forced universe.
   Same trades, honest theory, §12 re-anchored.
2. **Sequencing flaw: core deployed 8/14–17 blind, before the earnings wave
   prints 8/18+, could eat both supply themes** (consumer/retail + tech are
   ~the whole calendar). ACCEPTED: both core names now come from *outside*
   consumer-retail and mega-cap tech (industrials / healthcare / financials /
   energy relative-strength leaders), reserving both supply themes for the
   catalyst sleeve.
3. **Realistic qualified-setup count: 2–5, nearly all 8/19–8/29, desert
   after 9/4.** ACCEPTED — makes the 9/4 clock-pull nearly free, and the
   sleeve's empty state is expected, not a failure.
4. **Options {qualified catalyst ∩ sub-$40 ∩ §3.2 floors} ≈ empty; the
   budget likely goes unspent as designed.** ACCEPTED: piggyback-only rule
   deleted (see spec §6); an unspent budget is acknowledged as a legitimate
   outcome rather than carried as a fiction.
5. Verified calendar for the window: HD 8/18, TGT+LOW 8/19, WMT 8/20,
   NVDA+CRM 8/26, DLTR 8/27, AVGO 9/3; ORCL too late to trade; COST outside
   the window (the spec had implicitly overcounted it).

## Standing items for Chris (decisions only he can make)

1. §9 one-liner making the stop ratchet legal, or drop the ratchet.
2. §9 propagation of competition-capital anchoring into the §3 body text.
3. §3.7 ruling: does "no position held through a scheduled earnings report"
   bind only the position's *own* earnings (permitting sympathy-name puts /
   inverse exposure through NVDA's 8/26 print), or thematically (banning
   them)?
4. Pre-authorization (or rejection) of the 9/8–9/9 catch-up branch.
