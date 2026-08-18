# Comparative Research — AI Trading Strategies vs Ours

**Date:** 2026-08-13. Five independent research agents at Chris's direction:
(1) LLM autonomous-trading experiments, (2) small-account systematic evidence,
(3) institutional AI/ML fund records, (4) automation failure modes, (5) the
current 2025–26 AI retail meta. Full reports in the session transcript; this
is the synthesis and the record of what was adopted.

---

## Convergent findings (independently reached by ≥3 of 5 agents)

**1. Leash architecture beats model intelligence — the single most robust
finding in the entire literature.** LMArena/benchmark rank does not predict
trading rank (LiveTradeBench, StockBench). Alpha Arena S1: 4 of 6 frontier
LLMs lost 30–63% of real money in weeks; the autopsy was overtrading, fee
bleed, leverage, and abandoned risk discipline — behavioral failures, not
prediction failures. The only real-money LLM configuration beating a
benchmark at scale is human-owned rules + LLM-owned research. The
most-starred open framework independently converged on "the LLM never
touches execution." **Our design is the evidence-prescribed configuration.**

**2. LLM prediction ≈ random; LLM discipline is the value.** AIEQ (Watson,
9 years): underperformed on return and risk; the AI-ETF graveyard (AIIQ,
OAIE, DYNI, QRFT) confirms. AI hedge funds lag the S&P in aggregate and the
gap widens with adoption. Nobody with a verifiable record profits from AI
picking stocks over weeks — real ML profits live in execution, market
making, and short-horizon stat-arb, none accessible at $900. Sentiment alpha
measured its own decay: Sharpe 6.5 → ~1.2 in three years.

**3. Everything our manual bans is precisely what the documented evidence
says loses.** Pre-earnings long premium: −5 to −9%/event average, −10 to
−14% on high-vol prints (Review of Finance 2026). 0DTE: retail loses
~$350k/day. Margin-fueled concentration: the July 2026 −$1T semi drawdown
casualty list. Leverage killed every ruined Alpha Arena model. The one
documented *winning* retail strategy (selling premium) is banned for
solvency reasons the manual states correctly.

**4. Our two return engines are validated, conditionally.** 52-week-high
relative strength: best-evidenced retail-accessible tilt, valid in large
caps value-weighted. Post-earnings continuation: survives only in the
RS-leader + gap-held + day-1-2-entry form we require — and the current tape
mutes it (Q4-25: <50% of beats saw a positive next-day move; average beat
reaction +0.9%, half the norm). Low setup count is the expected state.

**5. The current regime rewards our posture.** Margin debt $1.53T,
+51.5% y/y — a growth signature previously seen only at the 2000/2007/2021
peaks. Breadth: <30% of S&P names beating the index. The field's defining
trade (concentrated AI-name momentum on margin) is working YTD and is the
documented casualty class when it unwinds. Cash discipline is currently
alpha, not idleness.

## The competitor's NVDA trade, quantified

NVDA ~$225.58; 8/26 print implies a ~6.75% move. Historicals: implied ≈
realized on average; actual exceeded implied only ~25% of the time recently;
typical move cooled to ±5.4%; **the last four prints were all beats and the
stock reaction was negative every time.** For 220/225 calls bought into peak
IV to profit after a 30–60% overnight IV crush, NVDA likely needs
~$235–240 — the top of the implied range, roughly a 1-in-4 outcome. One
month: they can absolutely beat us; the right tail is real. Repeated
monthly checkpoints: a −5 to −14% EV lottery re-entered every cycle on
margin, where the leaderboard evidence (S1 champion → −70% next season;
winners don't repeat) shows exactly how that sequence ends. The spec's
repeated-game framing is squarely supported.

## Adopted changes (all strategy rules — stricter than the manual, no §9 needed)

| # | Change | Source | Where |
|---|---|---|---|
| 1 | Option delta floor tightened to **Δ≥0.50** for long premium (manual's 0.35 remains the legal floor; 0.05–0.35 is the documented lottery-overpricing zone and 0.35 sits on its boundary) | Agent 2 | Spec §6 |
| 2 | Catalyst volatility ceiling measured on **post-gap ATR**, never the pre-earnings series (pre-gap ATR systematically passes names whose post-print vol makes a 10% stop a coin flip) | Agent 2 | Spec §5 |
| 3 | **Options sleeve default = unspent.** Evidence-backed uses only: (a) post-crush continuation calls 1–2 sessions after a qualified beat, when IV has reset 30–60% lower — the one moment long premium is cheap; (b) anti-field long puts. Long premium baseline EV is negative; the floors shrink the bleed, they don't create edge | Agents 2, 5 | Spec §6 |
| 4 | **NVDA-week guard (8/24–8/28): no adds, no new positions** while the field's correlated event resolves; de-grossing and anti-field expressions remain permitted | Agent 5 | Spec §5 |
| 5 | **Notional sanity check**: after preview, independently recompute qty × price × multiplier and 3-way compare (written intent, computed notional, previewed value); mismatch beyond rounding aborts | Agent 4 (Everbright/unit-confusion class) | Spec §7 |
| 6 | **Order-rate ceilings**: ~~max 5 placed orders/session~~ *(lifted by §9 amendment 2026-08-13 — see CLAUDE.md §4.10)*, max 2/symbol/session, max 3 replaces per resting stop/day; hitting any ceiling = stop placing, reconcile, log, wait for Chris | Agent 4 (Knight/runaway-loop class) | Spec §7 |
| 7 | **Identifier round-trip**: every symbol quoted via `get_quotes` immediately before ordering; returned description/price must match the written thesis; option symbols only via `create_option_symbol`, never from memory | Agent 4 (hallucinated-parameter class) | Spec §7 |
| 8 | **Stale-quote/halt gate**: quote timestamp checked against `get_datetime` before any order or stall/ratchet decision; reject if stale; tradability confirmed | Agent 4 | Spec §7 |
| 9 | **Long-session re-grounding**: any session older than ~3 hours re-reads CLAUDE.md and re-runs §4.5 reconciliation before its next order | Agent 4 (context-drift class) | Spec §9 |
| 10 | **Alert protocol**: alerts written to `ALERT.md` at repo root and committed; an unacknowledged alert at next session open = closing-only posture until Chris responds | Agent 4 (Knight's 97 unread emails) | Spec §9 |
| 11 | **Flatten drill** added to the execution-unlock test: enumerate open orders → cancel all → verify, rehearsed once before first real entry | Agent 4 | Spec §11 |
| 12 | **Log the field**: competitors' visible outcomes recorded each checkpoint — builds our own variance-vs-skill evidence for the moment a competitor's lottery ticket hits and amendment pressure arrives | Agent 5 | Spec §12 |

## Noted, not actioned

- **Two-name core concentration** is unsupported by the (portfolio-level)
  momentum literature — a scale constraint at $900, not an error. Do not
  quote portfolio statistics for an n=2 sample. Revisit as capital grows.
- **1x inverse ETF during NVDA/AVGO week** is our one legal direct
  field-down expression; the existing leveraged-sleeve gate (specific
  dislocation thesis, day-4 exit) and Chris's §3.7 own-earnings ruling
  already govern it. Available, not pre-committed.
- **Turnover check**: 5–15 orders/month at modern costs ≈ 1–2%/yr mechanical
  drag — cleared. The residual risk per incremental trade is selection
  quality, which argues for the fewest trades the strategy allows.
- Expectation calibration, stated once: at this scale the edges are real but
  small; friction and single-month variance dominate. The rules' realistic
  job is keeping the account intact for when variance pays — which is also
  the winning strategy for the repeated game.
