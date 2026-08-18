# Research Loop Design — the second tick

**Date:** 2026-08-14
**Status:** Approved by Chris ("Implement", 08:18 PDT) after four-question
design dialogue. Scope decisions below are his selections, verbatim from the
option labels.

## 1. What this is

A second recurring pass — the **research pass** — that runs alongside the §8
monitoring loop and maintains `research/candidates.md`: a tiered list of
potential moves (equities, catalyst setups, options) so the trading path
starts entry evaluation from researched candidates instead of from zero.

It is the playbook's missing half of the loop division: the tick answers
*"has the book drifted out of the box?"*; the research pass answers *"if
capital frees up or a setup ripens, what would we even look at?"*

## 2. Decisions (Chris, 2026-08-14)

| Axis | Decision |
|---|---|
| Mandate | **Both, tiered** — wide WATCH tier (ideas, unqualified) + narrow HOT tier (fully qualified against playbook rules). Only HOT can feed entries. |
| Cadence | **Independent second loop** — implemented initially as an interleaved cadence inside the main session's loop (see §5); promotable to a true second session if research passes crowd the tick loop. |
| Data sources | **Schwab + web** — read-only Schwab tools plus WebSearch/WebFetch for earnings dates, guidance details, and news context. |
| Consumption | **Research can ping** — advisory hot list, plus a rate-limited one-line ping in the main session when a candidate is HOT and the book has deployable capacity. No order ever originates in the research path. |

## 3. Components

- **`.claude/commands/research.md`** — defines one research pass: preconditions,
  the sweep, tier rules, the write, the return contract, and the parent-side
  ping gate. The command self-gates on cadence (a pass runs only if the last
  one is ≥ 45 minutes old), so it is safe to chain after every `/tick`.
- **`.claude/agents/research-scout.md`** — subagent that executes the pass.
  Tool surface: Read/Glob/Grep/Bash/ToolSearch, WebSearch/WebFetch, and the
  read-only Schwab tools (`get_datetime`, `get_market_hours`, `get_quotes`,
  `get_movers`, `get_instruments`, `get_option_chain`,
  `get_option_expiration_chain`, `get_advanced_price_history`,
  `get_advanced_option_chain`). **No order tools, no Write/Edit** — the file
  is maintained through `scripts/research-write.sh`, which writes only
  `research/candidates.md`. (Bash means file discipline is ultimately by
  instruction, as with tick-watch; the *order* prohibition is by harness.)
- **`scripts/research-write.sh`** — replaces `research/candidates.md` from
  stdin, after validating the content carries the required header and the
  never-a-source-of-order-parameters banner. Refuses anything else.
- **`research/candidates.md`** — the artifact. Committed at session close
  with everything else.

## 4. The artifact: tiers and rules

- **HOT** — passes *every* playbook gate as of the pass timestamp, with the
  qualification checklist written out line by line (§5 catalyst rules, §4
  tilts + volatility ceiling + sector carve-out, §3.2/§6 option floors),
  earnings date verified with source, corporate-actions check, reference
  price + quote timestamp, and an **expiry**: a HOT entry not re-verified
  within 1 trading session auto-demotes to WATCH.
- **WATCH** — stated thesis, sleeve it would belong to, what is missing to
  qualify, reference price + timestamp.
- **Tombstones** — evaluated and rejected, with the disqualifying reason and
  date, so passes don't re-research dead ends. A tombstone may be revisited
  only if its stated disqualifier has changed.

**Hard rule, stamped in the file header:** the hot list is never a source
for order parameters. Entry evaluation re-verifies everything live under
§4.9/§4.10; the list only chooses what to look at.

## 5. Loop mechanics

- Main session loop becomes `/tick` **then** `/research` each cycle; the
  research command no-ops in one file read when < 45 minutes have passed
  since the last pass (timestamp in the candidates-file header). One clock,
  no drift, and research inherits the tick loop's stop conditions for free.
- The scout is dispatched **in the background** so the tick cadence is never
  blocked by a research pass.
- One deeper **post-close pass** when the loop ends at `POST` — that is when
  §5 catalyst setups actually form — then stop.
- Preconditions per pass: unacknowledged `ALERT.md` → pass may run, **pings
  suppressed**; §3.6 Halt, restriction, or `cashCall` ≠ 0 → no passes (an
  account that cannot buy has no use for entry candidates).
- Per-pass budget: ~8 Schwab calls + ~4 web fetches. Ceiling, not quota.

## 6. The ping contract

Parent-side, after the scout returns. A ping fires only when **all** hold:

1. Candidate is HOT, verified in *this* pass.
2. Deployable capacity exists: settled cash for a minimum viable position,
   sleeve room, correlation not blocking (§3.8).
3. Not at §3.6 Halt (Halt → no passes at all). Halt is the only drawdown
   level as of 2026-08-17; the former Caution band, which blocked option
   pings, was removed per §9.
4. No calendar guard active (NVDA week 8/24–8/28 for adds, §8 endgame
   lockout approaching).
5. That candidate has not already pinged today, and today's ping count < 2
   (**max 2 pings/day, total** — counted from today's events corpus).

A ping is one line: symbol, sleeve, thesis, reference price. It is an
invitation to run the full §4.9/§4.10 entry discipline, which may and often
should conclude "no." Every ping outcome (acted / declined + reason) goes to
the decisions corpus; declined pings get counterfactual entries so the ping
mechanism itself gets scored by mid-window.

## 7. Never, in a research pass (§H analog)

- Never place, preview, replace, or cancel an order.
- Never write any file except `research/candidates.md`, via the script.
- Never promote to HOT without the written checklist.
- Never ping under suppression conditions or past the rate limit.
- Never treat an empty HOT tier as failure — **"zero qualified setups is a
  legitimate outcome"** (playbook §4) is in the scout's prompt verbatim.

## 8. Addendum 2026-08-14 — options ladder assessment + OI-delta scanner

Approved by Chris ("Do both", 08:41 PDT), from a day trader's suggestion to
watch options interest/volatility. Discussion split the idea in two; both
adopted, **neither is a new loop or timer** — open interest only updates
once daily (OCC overnight), so a fast clock would re-read a static number.

**8.1 Ladder/IV assessment** *(mandatory gate)* — before any option
candidate goes HOT, the scout assesses the chain as a whole, not just the
§3.2 contract floors: IV context (contract IV vs the underlying's ~20-day
realized vol — we only buy premium, so an IV spike means paying the top and
eating reversion even when the direction is right), spread quality across
the strikes around the candidate, OI distribution, day volume, and an exit
realism note (we must sell this contract later; a thin ladder is a bad exit
fill). Written into the HOT checklist. Elevated IV (IV/HV ratio well above
~1.3) defaults to reject unless the written thesis justifies paying up.

**8.2 Daily OI-delta scanner** *(idea source, skepticism baked in)* — the
post-close pass snapshots compact chain data for a bounded universe (held
underlyings + HOT/WATCH names, cap ~6) into `research/oi/DATE.jsonl` via
`scripts/oi-append.sh`, and diffs against the prior snapshot. A genuinely
large OI change (≥ +30% *and* ≥ +500 contracts on a contract, or a marked
aggregate put/call shift) may put the **underlying** on WATCH with the
observation noted — never straight to HOT, and never a trade trigger. The
evidence on "unusual options activity" is weak (mostly hedging, spread
legs, and market-maker flow in our liquid large-cap universe), and §3.7's
no-earnings-hold rule guts its classic use case; it is treated strictly as
an idea generator. First pass has no prior file and only writes the
baseline.

## 9. Promotion path

If research passes visibly crowd the trading loop (missed tick cadence,
context pressure), promote to a true second session: a separate terminal
running `/loop 45m /research`, pinging the trading session via SendMessage.
The command, agent, script, and artifact are identical in both modes; only
the dispatch changes. That promotion is operational, not a design change.
