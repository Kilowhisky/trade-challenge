# Close-of-market review — 2026-08-14 (day one)

**Scheduled by Chris 2026-08-13 evening:** "Let's plan a close of market
review tomorrow on our approach, variables, premises, etc…"

Run **after the closing bell and the §9 session-close protocol** (status
file written, stops confirmed, loop stopped). This is a conversation with
Chris, not a solo write-up — bring the data, he brings the judgment. The
data is the day's corpus: `status/data/2026-08-14-*.jsonl` (orders,
quotes, decisions, events — playbook §9 data-corpus rule), the tick
ledger, and the trade log. Decisions get scored against their recorded
reasoning, not against memory. Output:
a decisions section appended to this file, each item tagged *(strategy
rule)*, *(§9 ask)*, or *(no change)*.

---

## 1. What actually happened vs. the run sheet

- Fill quality: entry fills vs. quoted prices at §4.9 time (slippage per
  name, in cents and % — the screen assumed ≤ a few cents on USB/BMY
  spreads).
- The F drill: did place → approve → cancel-all → verify work first try?
  Discord approval latency per order (time from embed to ✅) — this is now
  a hard component of the fill→stop window.
- Stop placement: seconds from entry fill confirmation to stop confirmed
  resting, per name. The whole §3.4/§4.3 machinery is calibrated to this
  number.
- Anything that deviated from the run sheet, and why (honest log, §7.3).

## 2. Empirical verifications — close them or keep them open

| Assumption | How day one tests it | Status after today |
|---|---|---|
| `get_orders` filters by entered time (B3 window fix) | Tomorrow (8/15): does today's GTC stop appear with a wide window and vanish with `from_date=today`? Set up the check today, close it 8/15 | |
| `cashAvailableForTrading` semantics with unsettled proceeds | Only if something sells today (unlikely day one) — else stays open until first sale, conservative gate stays on | |
| Discord approval flow under real orders | Every order today | |
| `tick-watch` agent loads with MCP grants + Opus pin (smoke test = first dispatch) | First tick | |
| Quote staleness during RTH (§4.10 gate) — how often does it actually trip? | Tick ledger STALE count | |

## 3. Variables up for tuning (with the data that decides each)

- **Tick cadence** — Chris, pre-market: "We might not need 5 minutes."
  Evidence: today's tick ledger — count of rows, trips, and anything a
  10–15 min baseline would have caught later. Also per-tick token cost on
  Opus. Decision channel: *(strategy rule)*, playbook §8.
- **Stop geometry** — 10% trigger / 5% limit band vs. observed ATRs
  (USB ATR 1.6%, BMY 2.9% daily). Did intraday noise get anywhere near a
  trigger? Ratchet policy unused so far. *(§3.4 floor is manual — widening
  is a §9 ask; tightening is allowed any time.)*
- **Order-rate ceilings** — did 2/symbol/session bind or chafe anywhere
  real? The N+1 ruling and protective-order exception are new today —
  did either path actually execute, and cleanly? *(§9 territory.)*
- **Sleeve pacing** — playbook deploys core over days 1–2 (~43% today,
  target ~50%). Does the tape argue for finishing core on day 2, pausing,
  or re-screening? Catalyst sleeve opens 8/18 with the first qualifying
  prints — is the screen ready?
- **Discord approval timeout (600s)** — was any approval close to the
  limit? If Chris was slow to a ✅ once, what should the timeout/fallback
  be during a naked-position escalation? *(env var change.)*

## 3b. Guardrail counterfactuals — where did the rules bind?

Read `status/data/2026-08-14-counterfactuals.jsonl`. For each record:
what did the rule prevent or force, what would the unbound action be
worth right now (mark `ref_price` to the close), and what does it look
like at `score_at`? Apply the playbook's scoring discipline: symmetric
(blocked losers are guardrail wins), tail-risk rules scored on avoided
ruin not daily P/L, and conclusions feed calm §9 conversations only.
Day one candidates to watch: the 35% cap vs. desired sizing, the
2/symbol ceiling vs. any re-price urge, the entry-window discipline vs.
the opening tape, day-only entries vs. anything left unfilled at close.

## 4. Premises to re-examine (the uncomfortable list)

- **The momentum/near-high tilt** — USB/BMY were picked at 97%/87% of
  their 52-week ranges. One day proves nothing about the thesis — but did
  anything today (breadth, sector action, the prints themselves)
  contradict the *selection method*, not just the outcome? Guard against
  outcome-bias in both directions.
- **The reserve framing** — $900 reserve bridging T+1: any friction
  observed (settlement mechanics, buying-power reads) that the invariant
  math didn't anticipate?
- **Monitoring premise** — the loop covers 5–15% of the day by design;
  resting stops cover the rest. Did anything happen in a gap the loop
  would have caught? (If yes, that's an argument about cadence AND about
  what the stops alone can't do — §0's limits.)
- **Competition framing** — scoring is comp value at 9/14, but the
  standing rule is the repeated game: 9/14 is a checkpoint, not the end.
  Is anything in today's behavior optimizing for the checkpoint at the
  expense of the process? (This is the drift §0 warns peaks under
  pressure — check it while we're NOT under pressure.)
- **Process integrity** — three §9 rulings and two review rounds happened
  in one evening. Was any of today's execution confused by doc churn?
  Freeze candidate: no rule edits during market hours except via §E
  escalation needs.

## 5. Outputs

1. Decisions appended below, each tagged *(strategy rule)* / *(§9 ask)* /
   *(no change)* — with Chris's words for anything §9.
2. `status/2026-08-14.md` gets a one-paragraph pointer to this review.
3. Anything demoted/promoted in the watch table or cadence lands in the
   playbook the same evening, calm conditions, before day two's open.

---

## Decisions (filled at the review, 2026-08-14 ~16:40 ET)

Chris, verbatim: *"Lower tick latency. If there is a way for you to work
off stops without my input that would be approved. I will freeze as best
I can. But if something changes. I'll address it."*

1. **Tick cadence → 15 min baseline** *(strategy rule)* — for a book of
   stopped equities only. Reverts to 5 min whenever an entry order is
   working, any option position is open, or any §C.7 clock is inside 2
   days of its limit; 60s naked-position rule unchanged. Applied to
   tick.md §F. Evidence: 72 five-minute rows, 0 trips, ~2.4M subagent
   tokens on day one.
2. **Protective stops exempt from Discord approval** *(approved by Chris,
   quote above)* — implemented as a local patch to schwab-mcp's
   `place_previewed_order`: auto-approves only STOP/STOP_LIMIT orders
   whose every leg is an EQUITY SELL; everything else still gates.
   Reapplication doc + validation plan: `docs/patches/
   schwab-mcp-auto-approve-stops.md`. Takes effect next session; validate
   Monday pre-entry with a stop-replace drill. Closes the day-one gap
   where USB sat naked 3m32s on approval latency.
3. **Market-hours doc freeze** *(no change to rules; process norm)* —
   Chris will freeze rule/machinery edits during RTH "as best I can,"
   reserving the right to address genuine changes as they arise. §E
   escalation needs remain the standing exception.
4. Everything else reviewed: **(no change)** — stop geometry (10%/5%
   floors, ratchet unused day one), order-rate ceilings (never bound),
   sleeve pacing (core stays 2 names; BMY re-evaluated 8/17 only if it
   cleanly re-qualifies), Discord timeout 600s.
