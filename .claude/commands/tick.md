---
description: One read-only sweep of the playbook §8 watch table. The unit of the monitoring loop.
argument-hint: "[reason for this tick, optional]"
---

# /tick — one pass of the monitoring loop

A tick is a **read-only** sweep of the eight watches in playbook §8. It answers
one question: *has the book drifted out of the box since the last tick?*

A tick never places, replaces, or cancels an order. If a watch trips, the tick
**ends** and the escalation protocol in §E below takes over — under the full
§4.9 / §4.10 order discipline, not inside the loop's terse path.

Run this via `/loop <interval> /tick`. Interval per §F. When the research
loop is active, chain it: `/loop <interval> "/tick then /research"` —
`/research` self-gates on its own 45-minute cadence and is a no-op most
cycles (see `.claude/commands/research.md`).

## §Dispatch — run the sweep in the `tick-watch` subagent

When the Agent tool is available (it is, in normal sessions), the parent
does **not** execute §B–§D itself. Instead:

1. Parent checks the §A preconditions (they need parent context: `ALERT.md`
   acknowledgement state, re-grounding age, and any prior auth failure).
2. Parent dispatches the **`tick-watch`** agent
   (`.claude/agents/tick-watch.md`) with the cached inputs: account hash,
   today's `regularMarket` window, `recorded_hwm`, and the prior tick's
   position symbols + resting-stop map. Dispatch in the foreground — the
   next loop decision depends on the result.
   These are an optimisation, not a precondition. The scheduled tick runs
   this file with **no parent at all**, so the agent resolves every one of
   them itself: the hash per B2, the window per B1, `recorded_hwm` per B5.
   A missing input is something to look up, never a reason to return `FAIL`.
3. The subagent runs §B–§D, appends the ledger row, and returns the
   canonical line (+ `TRIP:`/`FAIL:` if anything needs the parent). Parent
   outputs the line to Chris **verbatim** and, on a trip, runs §E itself —
   escalation never happens inside the subagent.

Why: ~78 ticks of broker payloads would otherwise accumulate in the
trading session's context, and the subagent has **no order, Write, or Edit
tools** — so §H's "never place an order from inside the tick path" is
enforced by the harness rather than by instruction.

The **first dispatch of a session is the smoke test.** If it returns
`FAIL:` about missing Schwab MCP tools, the agent registration is stale
(definitions load at session start): fall back, in order —

1. A `general-purpose` subagent prompted to read and obey
   `.claude/agents/tick-watch.md` (full tool surface, so the read-only
   guarantee is by instruction, not harness — still gains the context
   isolation; validated live 2026-08-13).
2. Run §B–§D inline exactly as written below.

Genuine broker-read failures are different: two consecutive `FAIL:`s that
are not tool-registration problems count as failed broker reads under §G —
write the `BLIND` line yourself, `ALERT.md` if any position is open, stop.

---

## §A — Preconditions

Checked before every tick. Item 1 stops the loop; items 2 and 3 change how
the tick runs.

1. **The Schwab token has failed auth** → **abort and stop the loop**: write
   `ALERT.md`, notify Chris. A watchdog that cannot read the broker is not a
   watchdog.
2. **`ALERT.md` exists at the repo root and is unacknowledged** → closing-only
   posture (§4.5). Ticks continue — open positions still need watching — but
   no escalation may open a position; mark every ledger line `CLOSING-ONLY`
   in NOTE. If the alert is one §G lists as loop-stopping (restriction, halt,
   GFV — anything requiring Chris before *any* action), §G wins: stop.
3. **> 3 hours since the last re-grounding** → re-read `CLAUDE.md` and the
   playbook and re-run §4.5 reconciliation, then keep ticking. Late-session
   ticks must not rest on morning readings (playbook §9). Record `REGROUND`
   in that tick's NOTE — the ledger is the record of when re-grounding last
   happened.

---

## §B — The tick, step by step

Run these in order. The call ceiling: **five MCP calls on the first tick of a
session, four after** (B1's market-hours window is cached), minus one
whenever B4 is skipped (`PRE` tick or flat book — B1/B4 own those rules),
**plus one for the B2 `get_accounts` hash resolution whenever no hash was
supplied.** The scheduled tick is a fresh process every time, so it caches
nothing and resolves the hash itself: six is its normal count, not an overrun.
This is a ceiling, not a quota; never add a call to reach it. A watch that
needs more data escalates under §E instead.

### B1. Clock

Call `get_datetime`. Compare against the `regularMarket` start/end from
`get_market_hours` for today — fetched **once per session on the first tick
and cached**; the 09:30/16:00 boundaries do not move mid-session, and the
one field that does change (`isOpen`) is the one you must not use.

**Never gate on `get_market_hours` `isOpen`** — it reports whether the date is a
trading day, not whether the market is open now (it reads `true` at 23:20 ET).
Gate on the clock.

- Before 09:30 ET → tick is `PRE`. Continue; skip B4 quotes.
- 09:30–16:00 ET → tick is `RTH`. Full tick.
- After 16:00 ET → tick is `POST`. Run B2/B3/B5, write the line, then **stop
  the loop** and run the §9 session-close protocol.

### B2. Account read

`get_account(account_hash, include_positions=True)`.

**Resolving `account_hash` — never abort the sweep for want of it.** If the
dispatch prompt supplied a hash, use it. Otherwise call **`get_accounts()`**:
it takes no hash, and returns every account in the token's scope with its
`accountHash`. Exactly one account is in scope (the manual's closing note keeps
it that way on every re-auth), so that is the hash. Do **not** look for it in
`CLAUDE.md` — §7.4 redacts it to `HASH_REDACTED` because the repo is public, so
the manual records the identifier's *absence*, not its value; likewise no
`.env` or script carries it, by the same rule.

This is not a fallback, it is the normal path for the scheduled tick, which has
no parent session holding cached inputs and nobody to re-dispatch it. On
2026-08-27 two unattended sweeps returned `FAIL: no account hash` and left the
book unwatched for half an hour each, having never called `get_accounts`.

Read from **`currentBalances`**, never `initialBalances`:

| Field | Use |
|---|---|
| `liquidationValue` | account value — the §3.6 and §3.1/§3.2 denominator |
| `cashAvailableForTrading` | **settled cash** — but until its semantics are observed with unsettled proceeds present (first real sale), every buy gates on `cashAvailableForTrading − unsettledCash`, wrong only in the safe direction (playbook §7.8) |
| `unsettledCash` | reserve invariant, T+1 tracking |
| `cashBalance` | reserve invariant cross-check |
| `cashCall` | non-zero → §5 protocol, stop everything |
| `isClosingOnlyRestricted` | true → §5 protocol, stop everything |

### B3. Order read

`get_orders(account_hash, from_date=2026-08-14, to_date=tomorrow)`.

**`from_date` must cover every order that could still be resting, never just
today** — the call filters by *entered* time (per the tool contract,
schema-confirmed 2026-08-13; verify empirically once the first GTC stop has
rested overnight), so a stop entered on any prior day is invisible to a
today-only window and watch 4 would false-trip on every resting stop (and
then try to place a duplicate). `2026-08-14` is START_DATE from the CLAUDE.md
header; the requirement is the coverage, not the date — if the account ever
carries orders older than the window, widen it. **`to_date` must be
tomorrow** or today's orders are silently missed. Empty returns `[0]:`.

Record for each: status, symbol, side, quantity, order type, price.

### B4. Quote read

One `get_quotes` call carrying every held symbol — equities and option OSI
symbols together. Skip if flat. Option symbols come from the **B2 positions
payload** — the broker's own string, copied, never typed from memory.
`create_option_symbol` belongs to order paths (§4.10 identifier round-trip),
not to ticks; rebuilding an unchanging symbol ~78 times a session is waste.

Check the quote timestamp against B1. A quote more than a few minutes stale in
RTH is re-fetched once; if still stale, the tick is `STALE` and no escalation
that depends on price may proceed (§4.10).

### B5. Compute — before evaluating any watch

```
account_value = liquidationValue                   # THE §3.6 DENOMINATOR
comp_capital  = liquidationValue − 900.00          # ledger/display column only
hwm           = recorded_hwm                       # recorded values only — see below
halt          = 0.80 × hwm       # the only drawdown level (§3.6)
drawdown_pct  = (account_value − hwm) / hwm × 100
```

**Both figures, and do not cross them.** §3.6 was re-anchored to account value
on 2026-08-31 per §9, while the tick ledger's `comp_capital` column and the
canonical line keep their existing meaning so the file format stays readable
against every historical row. The drawdown and the halt test use
**`account_value`**; `comp_capital` is display only.

Crossing them is not a cosmetic error, it is a false halt. The two differ by
exactly the $900 reserve, so measuring `comp_capital` against an account-value
`hwm` reports roughly −24% on a flat book — through the −20% halt, every
session, permanently, with no position having lost anything.

**The HWM never ratchets intraday.** §3.6 defines it as the highest
account-value figure *recorded* to date; a transient bad print at 11:04
must not permanently raise Halt and then force loss-taking closes
when marks revert. New highs are recorded — and the HWM ratchets — at the
session-close status write (§7.2), from closing marks.

Per position, lifetime P/L is **computed, never read**:

```
lifetime_pl = marketValue − (averagePrice × abs(quantity))
```

**`unrealizedPL` in this payload is the current-day move, not lifetime.** A
position up 19% since entry has reported `unrealizedPL: -0.95`. Reading it
naively inverts the conclusion and would trip a stop-management decision the
wrong way.

`recorded_hwm` is resolved **once, on the first tick of the session, and
cached** (like B1's market hours). Its source is the **"State recorded —
current"** block of the most recent `status/YYYY-MM-DD.md` — that exact
heading; status files can contain superseded state blocks with near-identical
headings, and the first "High-water mark" grep hit may be stale.

**Resolve it with `scripts/latest-status.sh --hwm`, not by Globbing.**
`status/` is gitignored under §7.1 and the Glob tool returns no matches under
an ignored path — measured in the container 2026-08-26: 11 files present, `Glob
status/*.md` reported **0**. An agent that searches for the file concludes it
does not exist and falls back to something weaker, which for this particular
number means a wrong §3.6 halt threshold. The script reads only the "current"
block, so it also sidesteps the superseded-block trap above. The tick
ledger's `hwm` column is **derived output, never a source** — a ledger row
echoes what B5 computed, so reading it back would self-certify any error,
and the day's last row predates the session-close ratchet anyway.

Recovery rule: if the latest tick ledger is from a **prior day** and newer
than the latest status file, that session died before its §7.2 close write.
Use the **status-file HWM** for this session's thresholds — a dead
session's intraday marks are the least trustworthy numbers in the system,
and silently adopting a phantom print would ratchet Halt
permanently. But do not silently ignore them either: if the orphaned
ledger's highest `comp_capital` exceeds the status-file HWM, write
`ALERT.md` with that high and let Chris ratify the ratchet. The
unacknowledged alert puts the account in closing-only posture (§A.2), so
no new exposure is taken while the true HWM is in question. A same-day
ledger is not an orphan — a restarted session reuses the status file like
any other session open. HWM ratchets up only.

---

## §C — The eight watches

Evaluate in **this** priority order — which is not the §8 table's listing
order. A restricted account changes which responses are even legal, so it is
tested first; a naked position is the loop's top *actionable* alert.

| # | Watch | Trip condition | Action on trip |
|---|---|---|---|
| 1 | **Restriction** | `isClosingOnlyRestricted` true, or `cashCall` ≠ 0 | §5 protocol: no further orders, `ALERT.md`, notify Chris, account read-only. Stop the loop. |
| 2 | **Reserve invariant** | `min(cashBalance, cashAvailableForTrading + unsettledCash)` < $900.00 | Invariant breach. Halt all buys, `ALERT.md`, investigate before anything else. |
| 3 | **Drawdown** | `comp_capital` ≤ `halt` | §3.6 Halt — the only level; nothing trips above it. Notify Chris, stop the loop, no buys of any kind. |
| 4 | **Naked position** | any position with no matching resting stop in B3 | Place the stop **immediately** via §E. If it will not take, close the position (§4.3). This is the loop's reason for existing. |
| 5 | **Partial fill** | resting stop quantity ≠ filled position quantity | Replace the stop to match filled qty (§4.4). Max 3 replaces per stop per day (§4.10). |
| 6 | **Stop fill** | a position present last tick is gone, or its stop is consumed | Log the exit. Check for an orphaned remainder (§4.7). Redeploy cap = **actual proceeds**, not the pre-exit figure. |
| 7 | **Clocks** | option ≤ 7 DTE (warn) — **the close is at 5 DTE, §3.3 governs**; leveraged ETF ≥ day 3 of 5 (warn) — close on day 5, §3.5 governs; date ≥ 9/2 (flat by 9/4), ≥ 9/8 (lockout 9/10) — derived from END_DATE 9/14, re-derive if the NYSE calendar changes | Escalating warning from 2 days out; forced close at the limit per §3.3/§3.5 — the manual's numbers govern, not this row. These survive §3.6. |
| 8 | **Correlation** | weekly, first tick of the week: confirm the 60-day correlation result is **recorded in today's status file** (playbook §9 computes it at session open) | Not computed in-tick — it needs price-history pulls the tick budget forbids. Confirm against the written record, never from memory. If absent, finish the tick, then compute and record it outside the loop before any add. >0.7 cluster → adds blocked (§3.8). |

An entry order resting in B3 with no fill is not a trip — but it shortens the
interval (§F).

---

## §D — Write the ledger line, then output one line

Append to the tick ledger:

```
scripts/tick-append.sh <DATE> <TIME_ET> <STATE> <COMP> <HWM> <DD%> <LEVEL> \
  <POS> <STOPS> <ORDERS> <SETTLED> <UNSETTLED> <RESERVE> <FLAGS> ['NOTE text']
```

`DATE`/`TIME_ET` come from B1 (Eastern), never from the machine clock — this
box runs Pacific. `STATE` is `RTH`/`PRE`/`POST`/`STALE`/`BLIND`. `LEVEL` is
`OK`/`HALT`. `RESERVE` is the watch-2 total-cash figure in dollars
(e.g. `900.00`), never the word "ok". `FLAGS` is `-` when clean, else the
tripped watch numbers. `NOTE` carries, space-separated as applicable: the
`CLOSING-ONLY` marker (§A.2), the `REGROUND` marker (§A.3), and `$ARGUMENTS`
when this tick was run for a stated reason. Omit it only when none apply.
**Pass the note as a single `'single-quoted'` argument** — shell
metacharacters in bare free text cannot be repaired downstream (details in
the script's usage header).

The script prints the canonical one-line summary, derived from the same
fields as the ledger row. Output **that line to Chris verbatim, and nothing
else**:

```
14:35 ET | RTH | comp $912.40 (+1.4%) | HWM $900.00 | OK | 2 pos / 2 stops | 0 orders | settled $318.00 | reserve $900.00
```

No narration, no restatement of what was checked, no "all clear" paragraph.
Roughly 78 ticks fit in a session; a verbose tick eats the context needed for
the actual trading decisions later in the day. A clean tick is one line.

On a trip, output the line, then the escalation under §E.

---

## §E — Escalation (a watch tripped)

The tick is over. This is a normal trading action and takes the full
discipline — the loop's terseness does not carry over.

1. **Stop the loop clock.** No further ticks until the book is clean.
2. **Re-ground.** Re-read the **playbook** — mandatory before *any* order per
   the manual's header, however fresh the session. If the session is > 3
   hours from its last re-grounding, also re-read `CLAUDE.md` and re-run
   §4.5 reconciliation.
3. **Write the §4.9 pre-trade check into `trade-log.csv` before the order** —
   instrument permitted, size within caps, quality floors, earnings and
   corporate actions, stop planned, settled funds, drawdown level.
4. **Clear all four §4.10 gates**: notional sanity three-way compare;
   identifier round-trip via `get_quotes`; order-rate ceilings (2 orders per
   symbol per session, 3 stop replaces per day — read §4.10 for the current
   numbers rather than trusting this parenthetical); stale-quote and halt gate.
   An order that cannot clear them is not placed.
5. **Place it.** Limit orders only (§4.1). Verify the fill by re-query (§4.8).
   Retry bounds: a §3.4 stop gets **3 placement attempts**, a §4.3 close
   **2**. Past either bound: place nothing more, write `ALERT.md`, notify
   Chris, stop the loop (§G). "Protective" never licenses unbounded retries.

   **These bounds are not the §4.10 replace ceiling.** That ceiling (3 per
   resting stop per day) governs *amendments to an already-resting stop*. A
   stop that has never rested is a **new order** and consumes a per-symbol
   slot — so after the entry fill, the first stop attempt is the symbol's
   second routine order of four. The §4.10 per-symbol ceiling was raised to
   4 on 2026-08-17 precisely so an entry plus its stop no longer consumes the
   whole allowance — a stop retry or an entry re-price is now routine rather
   than an immediate breach. Past the ceiling, §4.10's protective-order
   exception still applies and still counts as a ceiling hit: reconcile,
   write `ALERT.md`, notify Chris, place nothing discretionary afterward.
   Read §4.10 for the current numbers rather than trusting this paragraph.
6. **Restore the invariants**: stop matches filled quantity, no orphaned stops
   (§4.7), no working entry orders left to rest past session end (§4.2).
7. **Log it**: `trade-log.csv` row, `ALERT.md` if Chris is needed, commit.
8. **Resume the loop** only once the book state is clean, at the §F interval.

---

## §F — Cadence

The interval is a function of book state. Re-evaluate it after every tick.

| Book state | Interval |
|---|---|
| Naked position — filled, stop not yet confirmed resting | **60s** until the stop is confirmed, or until 3 failed stop attempts / 15 minutes naked force the §4.3 close — retry bounds and the ceiling interaction per §E step 5 and §4.10's protective-order exception |
| Working entry order resting, unfilled | 5 min — catch the fill→stop window |
| Positions open, all stops confirmed matching | **15 min** baseline. Any option position open, or any clock inside 2 days of its limit (§C.7) → back to 5 min |
| Flat, no working orders | **Stop the loop.** Cash is a position (§3.9) and idle ticks burn context |

---

## §G — Stop the loop entirely

- Session end (B1 `POST`), or Chris ends the session
- §3.6 **Halt** breached
- `cashCall` ≠ 0, or `isClosingOnlyRestricted` true
- Unacknowledged `ALERT.md` that requires Chris before any further action
- §4.10 order-rate ceiling hit — reconcile against the broker, log, wait for Chris
- A protective order (§3.4 stop or §4.3 close) exhausted its §E retry bound
- Two consecutive failed broker reads → write a `BLIND` line, `ALERT.md` if any
  position is open, stop. Never infer state you could not read (§4.5).

---

## §H — Never, in a tick

- Never place, replace, or cancel an order from inside the tick path — §E only
- Never read `unrealizedPL` as lifetime P/L — compute it (B5)
- Never gate on `get_market_hours` `isOpen` — gate on the clock (B1)
- Never read `initialBalances` — `currentBalances` only (B2)
- Never call `get_orders` with a today-only window — `from_date` is the
  competition start and `to_date` is tomorrow (B3)
- Never type an option symbol from memory — in a tick, copy it from the B2
  positions payload; in an order path, build it with `create_option_symbol` (B4)
- Never treat `buyingPower`-style aggregates as spendable — settled cash only (§5)
- Never write to `trade-log.csv` from a clean tick — the tick ledger is separate
- Never skip a §4.10 gate for speed, including during a 60s naked-position tick

---

The Schwab payload quirks quoted in this file (`unrealizedPL`, `isOpen`,
`currentBalances`, the `get_orders` window) mirror the `schwab-mcp-notes`
skill, which is the **source of truth** and carries the verification dates.
If this file and the skill disagree, the skill wins — and the disagreement
is a defect to fix here.
