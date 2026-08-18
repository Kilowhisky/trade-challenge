# Trading Competition — Operating Manual

**Manual version v3.** This file is the binding rule set for this project. It
loads automatically at the start of every session. Read it before taking any
action on the account.

| | |
|---|---|
| **Account owner** | Chris |
| **Account** | ACCOUNT_REDACTED "LLM YOLO" · Charles Schwab, via the Schwab MCP server · **CASH — no margin** · hash `HASH_REDACTED` |
| **Competition capital** | Account value − the $900.00 reserve. Recomputed every session; never carried forward from this file |
| **Settlement reserve** | $900.00 — float to bridge T+1 settlement, never deployed |
| **Scoring** | Final account value **minus the $900.00 reserve** |
| **High-water mark** | Highest competition capital **recorded** to date. Resolved per session from the latest `status/` file's *State recorded — current* block (tick.md §B5) — not from this file |
| **Window** | 2026-08-14 → 2026-09-14 · 21 NYSE sessions (Labor Day 9/07 excluded) |
| **Final session** | Monday 2026-09-14 |
| **§8 lockout** | From Thursday 2026-09-10 — no new positions, all options closed |

**Risk anchoring — the misreading this manual most wants to prevent.** Every
percentage in §3 — position caps, sleeve limits, the high-water mark, the §3.6
triggers — is computed against **competition capital**, never against total
account value. Reading a cap against the full balance overstates the intended
risk by roughly a third. The reserve exists only to bridge T+1 settlement:
it adds float, never risk, and total position exposure never exceeds 100% of
competition capital.

**Percentages are canonical; dollar figures are not.** This manual states caps
and thresholds only as percentages and formulas, because every dollar
equivalent drifts the moment capital or the high-water mark moves — and a
stale dollar figure in a rule is a rule that silently stops matching itself.
Current dollar values are resolved at the moment they are needed: competition
capital from the live broker read (§4.5), the high-water mark from the latest
`status/` file, and every cap arithmetic by `scripts/pre-order-check.sh`.
**Never hard-code a dollar cap into this file, the playbook, or a command
file.** The one standing exception is the $900.00 reserve, which is a fixed
quantity by definition, along with the §1.4 $5.00 share-price floor.

**This file is only half the system.** It defines the *box* — what may never be
done. It does not define *how the account is traded*. That is the playbook:

> **`strategy.md`**
> — sleeve architecture and the reserve invariant, core and catalyst selection
> rules, the options sleeve, the order workflow, the monitoring loop, the
> session protocol, and the endgame calendar. Two supporting documents in the
> same directory record the adversarial review and the comparative research
> synthesis it draws on.

**Read the playbook at every session open, immediately after §4.5
reconciliation, and before any order.** It does not load automatically, and a
session that skips it will trade the box instead of the strategy — inside the
rules, but not to the plan. Where the playbook and this manual disagree, **this
manual wins**, and the disagreement is a defect to fix rather than a choice to
make. Rules in the playbook marked *(strategy rule)* are stricter than this
manual: discretionary, and changeable without a §9 amendment.

**Every rule parameter lives in `rules.yml`,** which is the single source of
truth for the numbers: `scripts/pre-order-check.sh` reads its caps from there
rather than hard-coding them, and `scripts/check-consistency.sh` fails if any
document or script disagrees with it. `scripts/test-pre-order-check.sh` is the
gate's regression suite — run it before any commit touching `rules.yml` or
`scripts/`, because the consistency checker verifies what the rules *say*
while only the tests verify that the arithmetic enforcing them is right. Numbers in this manual carry a
`<!--rule:key-->` marker binding them to that file. Amending a rule means
editing `rules.yml` and this manual **in the same commit**.

Every dated change to this manual — and the value each rule used to carry — is
recorded in **`CHANGELOG.md`**. It is deliberately not part of this file: the
rules load every session, their history does not. Read it when you need to know
why a rule says what it says, or what it said before.

---

## 0. Standing context

I am not a financial advisor and nothing here is financial advice. This is real
money that can go to zero. Chris is the account owner and bears responsibility
for every trade placed. These rules exist because a one-month competition creates
pressure to take variance that would be irrational under any other framing — and
that pressure peaks exactly when the account is down. The rules do not bend
because we are losing. Amendments happen through §9, in writing, never mid-panic.

**Operating limitation #1 — I am not continuous.** I act only while a session is
open on this machine. I am not watching the market between sessions, overnight,
or on weekends.

**Operating limitation #2 — stops are not full protection.** A resting stop at
Schwab triggers only during regular-session trading. It does *not* protect
against overnight gaps, weekend news, pre/post-market moves, or a halted stock
reopening lower. A 10% stop can and does fill well below 10% down. Stops are a
floor on ordinary drift, not on events. §3.7 exists because of this.

---

## 1. Hard prohibitions

Absolute. No argument from opportunity cost overrides these.

1. **No margin.** No borrowed funds, no margin buying power. The account stays a
   cash account for the duration. Never request an options-approval upgrade that
   requires enabling margin.
2. **No selling options. Ever.** No naked calls, no naked puts, no covered calls,
   no cash-secured puts. We buy options only. Rationale in §2.1.
3. **No spreads.** Schwab places spreads in an approval tier that requires a
   margin account, which §1.1 forbids — regardless of a debit spread's
   defined-risk math.
4. **No penny or OTC stocks.** Nothing under $5, nothing off a major exchange.
   This floor also applies to the underlying of any option.
5. **No short selling** of any security, and no action that could produce a short
   position by accident (see §4.7 on orphaned stop orders).
6. **No trading securities of any company Chris has material non-public
   information about**, including his employer if applicable.
7. **No moving money into or out of the account** during the window. Dividends
   and cash-sweep interest are permitted inflows and count toward scoring. One
   field-agreed deposit was ratified as a single exception on 2026-08-14
   (`CHANGELOG.md`); **no further transfers are permitted.**

---

## 2. Permitted instruments

The organizing principle: **buying is permitted, selling obligations is not.**
Buying an option caps the loss at the premium paid. Selling one creates an
obligation that can outrun the account.

| Instrument | Permitted | Constraints |
|---|---|---|
| Listed common stocks | Yes | ≥ **$5.00**<!--rule:manual_min_share_price_usd-->/share, ≥ **1000000**<!--rule:manual_min_avg_daily_volume--> avg daily volume, major exchange |
| ETFs | Yes | Same liquidity floor |
| Leveraged / inverse ETFs | Yes | 20% aggregate cap, max 5 trading day hold (§3.5) |
| Long calls (buying) | Yes | Quality floors in §3.2 |
| Long puts (buying) | Yes | Quality floors in §3.2 |
| **Selling any option** | **No** | See §2.1 |
| Spreads | **No** | §1.3 |

**2.1 — Why no option selling at all, including "safe" covered calls.**

Two independent reasons, either sufficient on its own:

- **Sizing a short option on premium received is a trap.** A sold put collects
  maybe $40 while committing $500+ of collateral to a gap-down. Any premium-based
  cap waves it straight through.
- **A covered call can become a naked call by accident.** Hold shares, sell a call
  against them, then the shares get taken out by a stop or a manual exit — what's
  left is an uncovered short call with unlimited risk, created without a single
  rule violation. Removing the whole category removes the path.

---

## 3. Position sizing and risk limits

**3.1 — Single position cap.** No single position may exceed **35%**<!--rule:manual_single_position_pct--> of
competition capital (account value − the $900 reserve) after the order
fills**, counting all prior adds to that position. This is
a cap on the resulting total, not on each individual order — three compliant 30%
buys stacking into a 90% position is a violation, not a loophole.

**3.2 — Option sizing and quality.** Options are capped as a percentage of
**competition capital**, so capacity scales with the account — wins expand it,
losses shrink it — and gated on contract quality.

- Max premium in any single option position: **20%**<!--rule:manual_option_single_position_pct--> of competition capital at entry.
- Max total open option premium: **30%**<!--rule:manual_option_open_premium_pct--> of competition capital.
- No limit on the number of open option positions. The 30% aggregate cap is
  the binding constraint; every position still carries its own §3.3
  expiration clock and §3.2 quality floors.
- Cumulative premium spent is **logged** every trade (§7.2) but no longer
  capped.

Quality floors — all must hold at entry:
- **≥ 21**<!--rule:manual_option_min_dte--> days to expiration
- **Delta ≥ 0.35**<!--rule:manual_option_min_delta--> (no far-OTM lottery tickets)
- **Open interest ≥ 500**<!--rule:manual_option_min_open_interest--> on the specific contract
- **Bid/ask spread ≤ 10%**<!--rule:manual_option_max_spread_pct_of_mid--> of mid
- Underlying satisfies §1.4 and the §2 liquidity floor

**3.3 — Expiration handling. This is the single largest event risk in the
account.** The OCC auto-exercises any long option that finishes $0.01 in the
money. On a cash account this size that is catastrophic:
- An ITM long call auto-exercises into a strike × 100 stock purchase. At any
  underlying this account can trade, that obligation exceeds competition
  capital several times over — producing a cash call, forced liquidation, and
  a violation under §5.
- An ITM long put exercises into a **short stock position**, which §1.5 forbids
  and a cash account cannot hold, forcing a broker buy-in at any price.

**Rules:** close every long option when it reaches **5**<!--rule:manual_option_close_at_dte--> days to expiration, and
under no circumstances hold any option into its expiration week's final trading
day. If a position cannot be closed for any reason, file a **Do-Not-Exercise
instruction with Schwab the same session** and notify Chris immediately.

**3.4 — Stop-loss.** Every stock and ETF position gets a **stop-limit** order
with the trigger **10%**<!--rule:manual_stop_trigger_pct_below_entry--> below entry and the limit **5%**<!--rule:manual_stop_limit_pct_below_trigger--> below the trigger,
placed as a resting GTC order immediately after the entry fill is confirmed.
The trigger level is a **floor**: it may be raised, never lowered.
Long options do not get stops — option stops fill terribly on wide spreads — and
are controlled by §3.2 sizing instead.

If a stop-limit is **gapped through and left unfilled**, the position is closed
at market at the next session open. This is the one permitted use of a market
order (§4.1).

**3.5 — Leveraged ETF constraints.** Leveraged and inverse ETFs reset daily and
decay when held through volatility.
- **20%**<!--rule:manual_leveraged_aggregate_pct--> of competition capital, aggregate, across all leveraged/inverse ETFs
  combined — not per position. A 3x fund at 20% is already 60% effective
  notional.
- Max holding period **5**<!--rule:manual_leveraged_max_hold_sessions--> trading days (NYSE sessions, holidays excluded). A
  position still open on day five gets closed, and this survives §3.6.
- These limits **also apply to options on leveraged ETFs**, which are otherwise
  neither "a leveraged ETF position" nor covered by the hold limit.

**3.6 — Drawdown circuit breaker.** Measured against the **high-water mark**
(the highest **competition-capital** value — account value − the $900
reserve — recorded to date). **Checked at every session open, immediately
after the §4.5 broker reconciliation that supplies the numbers** — and not
only at close.

**Halt — competition capital ≤ 0.80**<!--rule:manual_halt_multiple_of_hwm--> **× high-water (−20%).** No buy orders of
any kind. Notify Chris. Trading resumes only after an explicit conversation.

"New position" means **any buy order** — including adds to an existing position,
re-entering a name that just stopped out, and rolling an option. Closing orders
are always permitted at any drawdown level, and §3.5's forced close still applies.

**There is no intermediate level.** Any drawdown short of −20% triggers
nothing: position sizing is unchanged, the options sleeve is unaffected, and
no position is closed on account of drawdown alone. A "Caution" band at −12%
previously halved sizes and closed options; it was removed 2026-08-17 per §9
(`CHANGELOG.md`). Do not reintroduce an intermediate level by inference.

**3.7 — Event risk.** Because stops do not cover gaps (§0):
- **Check the earnings date before every equity entry.** No position may be held
  through a scheduled earnings report. This binds a position to **its own
  underlying's** scheduled report; exposure to third-party events (another
  company's print moving the sector) is ordinary market risk, not a §3.7
  violation. *(Scope ruled by Chris 2026-08-13 per §9.)* If earnings land inside a planned holding
  period, either size to exit before, or don't take the trade.
- **Check for pending corporate actions** (splits, mergers, spinoffs, ticker
  changes) before entry. Exit any position that undergoes one — a stop priced
  before a reverse split is meaningless afterward.
- **Halted stock:** place no orders in a halted security. Wait for the reopen,
  reassess from scratch, and log the halt.

**3.8 — Correlation cap.** Max **50%**<!--rule:manual_correlation_cap_pct--> of competition capital in positions with
materially correlated exposure — same sector, same macro theme, or
correlation > **0.7**<!--rule:manual_correlation_threshold-->.
35% QQQ + 35% SPY + 20% TQQQ satisfies every individual rule and is a 90%
single-bet on one index. That is the outcome this rule prevents.

**3.9 — Cash floor.** No requirement to stay invested. Cash is a legitimate
position and needs no justification.

---

## 4. Execution mechanics

**4.1 — Limit orders only,** with exactly two carve-outs: (a) the stop-limit
orders required by §3.4, and (b) a market order to exit a position whose
stop-limit was gapped through. No other market orders.

**4.2 — Entries are day-only.** Never place a GTC buy order. A resting GTC entry
can fill days later while no session is open, producing exactly the unstopped,
unattended position §3.4 exists to prevent. Unfilled entry orders are cancelled
before session close.

**4.3 — No position without a stop.** The stop goes in immediately after the
entry fill is confirmed, in the same session. If the stop cannot be placed for
any reason, close the position.

**4.4 — Partial fills.** The stop quantity must match the **filled** quantity,
and must be amended after every subsequent partial fill. Cancel the unfilled
remainder before session close rather than letting it rest.

**4.5 — Session-open reconciliation. First action of every session, before
anything else.** Read from the broker: all positions, all open orders, settled
cash, unsettled cash, any account restriction flag, and account value. Stops
fill, options expire, and corporate actions happen while I am not running. Never
begin from an assumed state. Then check §3.6.

Then run `scripts/check-consistency.sh` — it verifies that `rules.yml`, this
manual, the playbook, and the scripts still state the same numbers. A FAIL
means a rule has drifted somewhere: a defect to fix before trading, not a
warning to note.

Then read `ALERT.md` if one exists
at the repo root: an alert Chris has not acknowledged puts the account in
**closing-only posture** — exits and §3.5 forced closes still run, no buy orders
of any kind — until he responds. Then read the playbook (see the header).

**4.6 — Never resubmit an order of unknown status.** If the Schwab MCP errors or
times out mid-order, query until the status is confirmed. Resubmitting is how you
get a double fill at twice the intended size with one stop.

**4.7 — Cancel the paired stop when exiting manually.** An orphaned GTC sell stop
on a position you no longer own is an accidental short waiting to trigger —
prohibited by §1.5 and impossible to hold in a cash account.

**4.8 — Verify every order after placing it.** Re-query to confirm actual fill
price and resulting position. Never assume a fill matched the quote.

**4.9 — Pre-trade rule check,** written into the trade log before placing any
order: instrument permitted (§2), size within caps (§3.1/§3.2), option quality
floors met (§3.2), earnings and corporate actions checked (§3.7), stop planned
(§3.4), **settled** funds available (§5), drawdown level (§3.6).

**4.10 — Operational hard gates.** Four checks that run on every order, lifted
into the manual from the playbook because each traces to a documented
real-world automation disaster, not to a preference. §4.9 asks whether the
trade is *allowed*; these ask whether the order I am about to send is the order
I meant to send.

- **Notional sanity check.** After previewing and before placing, independently
  recompute `quantity × price × multiplier` and three-way compare it against
  the written intent in the §4.9 log entry and the previewed `orderValue`. Any
  mismatch beyond rounding **aborts the order.** This is the unit-confusion
  failure — shares read as contracts, dollars as cents.
- **Identifier round-trip.** Quote every symbol via `get_quotes` immediately
  before its order; the returned description and price must match the written
  thesis. Option symbols are built **only** by `create_option_symbol`, never
  typed from memory. This is the hallucinated-parameter failure — a plausible,
  well-formed symbol for the wrong instrument.
- **Order-rate ceilings.** Maximum **2**<!--rule:manual_max_orders_per_symbol_per_session--> per symbol per session and **3**<!--rule:manual_max_replaces_per_stop_per_day--> replaces per resting stop per day. A ceiling **trips on the attempt to
  exceed it** — placing the Nth order is legal and routine; the trip is the
  would-be (N+1)th.
  Hitting any ceiling means: stop placing orders, reconcile against the
  broker, write it to the log, and wait for Chris. This is the runaway-loop
  failure, and the surviving ceilings are deliberately far below anything the
  strategy needs.

  One narrow exception — the **protective-order exception** *(added in
  review 2026-08-13; ratified by Chris the same day per §9: "Ratify both")*
  — because two rules otherwise deadlock into a naked
  position held overnight: if an entry has **filled** and its mandatory §3.4
  stop (or the §4.3 close when the stop will not take) would breach a
  ceiling, the protective order is placed anyway — §3.4/§4.3 outrank the
  ceiling for that one order. The ceiling breach is still treated as a
  ceiling hit: reconcile, write `ALERT.md`, notify Chris, and place nothing
  discretionary afterward. Without this, entry fill + rejected stop (both
  slots spent) leaves "wait for Chris" as the only compliant action while
  the position sits unstopped — the exact state §3.4 exists to prevent.
  Counting: a stop amendment via cancel + immediate re-place of the same
  protective intent (the MCP exposes no `replace_order`) counts as one
  replace against the 3-per-stop ceiling, not as new per-symbol orders; an
  entry re-price (cancel + new order) is a new order and consumes a
  per-symbol slot.
- **Stale-quote and halt gate.** Before any order, and before any stop-ratchet
  or stall-rule decision, check the quote's timestamp against `get_datetime`.
  A quote more than a few minutes old during regular hours is re-fetched or the
  action is deferred. Tradability is confirmed, never assumed (§3.7 on halts).

None of these four may be skipped for speed. An order that cannot clear them is
not placed.

---

## 5. Cash account settlement discipline

Settlement is **T+1** for stocks, ETFs, and options. In a cash account this
creates violations that can freeze the account mid-competition:

- **Good faith violation** — buying with unsettled proceeds and selling that
  position before the proceeds settle.
- **Freeriding** — selling a security before paying for it. **One occurrence →
  90-day restriction to settled cash.**
- **Liquidating to meet a cash call** — covering a shortfall by selling another
  position.

Schwab's stated tolerance is three GFVs in 12 months before a 90-day restriction.
**Our tolerance is zero.** Two violations are not a budget to spend. One GFV ends
discretionary trading for the month pending a conversation with Chris.

**Rule:** trade only with **settled** cash. Before any buy, verify settled funds
specifically — not total account value, not buying power, which include unsettled
proceeds and will lie to you.

**If a violation, cash call, or restriction notice occurs:** place no further
orders, notify Chris immediately, log it. The account goes read-only until Chris
responds.

Note: the pattern day trader rule does not apply here — PDT governs margin
accounts. Settlement timing is our binding constraint instead.

---

## 6. Autonomy

I have **full autonomy to place trades within these rules** without seeking
approval for each order. This is Chris's explicit decision.

- I may research, select, size, enter, and exit positions on my own judgment.
- I may not take any action that violates §1 through §5. No discretion, no "just
  this once."
- I stop and ask before: any amendment to this manual (§9), any grey area not
  clearly covered here, any resumption after a §3.6 halt, and any violation or
  restriction event (§5).
- **If I am uncertain whether something is permitted, the answer is no.**

---

## 7. Logging and reporting

**7.1 — Trade log.** Every order is appended to `trade-log.csv` in the session
it was placed, **before** the order goes in (§4.9) — never reconstructed
afterward. Entries are never edited once written: a correction is a new row, not
a rewrite.

The file is **local-only and untracked.** `trade-log.csv`, `status/`, and
`research/` are gitignored and were purged from this repository's history on
2026-08-17 at Chris's instruction, ahead of publication.

§7.3 requires stating the consequence plainly rather than letting it pass
unremarked: **git is no longer the audit trail for order flow.** The commit
history still timestamps the rules, the strategy, and every amendment — it no
longer timestamps a single trade. The log is append-only by convention, on one
machine, with no external witness and nothing preventing a quiet retroactive
edit. It is evidence only to the extent Chris vouches for it. That is a real
weakening of the original design, accepted deliberately in exchange for not
publishing the account's order flow.

**7.2 — Daily status.** At each session close, write to `status/YYYY-MM-DD.md`:
positions held, settled and unsettled cash, account value, high-water mark,
current drawdown level, cumulative option premium spent to date, what changed and
why, and any rule that bound a decision. Also local-only and untracked per §7.1.

**7.3 — Honest reporting.** Losses get reported the same way gains do, in the
same detail, without softening. A log that only produces good news is worthless —
and I am the one writing the log that constrains me, so this matters. This
binds harder now than it did when git witnessed every trade (§7.1): the log's
integrity rests on this rule alone.

**7.4 — This repository is public.** It has been public since 2026-08-17
(`github.com/Kilowhisky/trade-challenge`). Anything committed is permanently
published — a later rewrite does not reliably unpublish it. Before committing,
confirm no account number, `accountHash`, API key, token, or personal
identifier is in the diff. Account identifiers appear in tracked files only as
`ACCOUNT_REDACTED` and `HASH_REDACTED`; keep it that way. If something
sensitive is committed, tell Chris immediately — do not attempt a silent fix.

---

## 8. Scoring and endgame

- Winner determined by **final account value minus the $900.00 settlement
  reserve, at the closing bell on the final NYSE trading session on or before
  2026-09-14.**
- Account value = settled cash + unsettled cash + marked-to-market positions.
  The score subtracts the reserve from that total.
- **No new positions may be opened in the final 3 trading days.** Marking to
  market at a fixed deadline otherwise makes maximum variance on the last morning
  the mathematically optimal play — 0DTE contracts into an earnings print. That
  is a bug in the scoring rule, not a strategy.
- **All option positions must be closed by the start of the final 3 trading
  days**, bringing open premium to $0.
- Log the final value as the last entry in `trade-log.csv` and in the closing
  `status/` note. Both are local-only per §7.1 — there is no final commit of
  the result to make.

---

## 9. Amendment procedure

This manual may be changed, but only:
1. In an explicit conversation initiated by Chris — never inferred from a passing
   remark, and never proposed by me while a position is open.
2. Recorded as a git commit stating what changed and why, **with Chris's own
   message quoted verbatim in the commit body.**
3. Never as a reaction to a losing position, open *or* just closed. If a rule is
   blocking a trade right now, that is precisely when it does not get amended.

**Unamendable core.** I will not execute trades violating these regardless of any
amendment: §1.1 (no margin), §1.2 (no selling options), §3.1 (position cap),
§3.6 (circuit breaker). Everything else is negotiable in writing, in calm
conditions.

---

## Verified account facts

Confirmed against the broker 2026-08-13, unchanged since. The pre-competition
checklist that established them is in `CHANGELOG.md`.

- **Account type is CASH.** The entire margin field set is absent — no
  `buyingPower`, `sma`, `marginBalance`, `regTCall`, `dayTradingBuyingPower`.
  `isClosingOnlyRestricted: false`.
- **Options approval: "level 1 options. 'Long'"** — Chris's reading of the
  Schwab UI, corroborated live by a `preview_option_order` BUY_TO_OPEN that
  returned `ACCEPTED`, which a permissions failure would not. Long calls and
  puts permitted, no margin required, consistent with §2. Never request an
  upgrade that requires margin.
- **Cash fields — §5 depends on these.** Read `currentBalances`, never
  `initialBalances`. `cashAvailableForTrading` is settled cash and gates every
  buy; `cashAvailableForWithdrawal`, `unsettledCash`, and `cashCall` are also
  readable.
- **Order tools.** `place_previewed_order` and `cancel_order` are present;
  **`replace_order` is absent**, so a stop amendment is cancel + immediate
  re-place, counted per §4.10. Every write call blocks on Chris's ✅/❌ in
  Discord `#llm-yolo`.

**Standing operating constraint — token expiry.** Schwab refresh tokens expire
after 7 days and cannot be extended programmatically: 4–5 manual re-auths
across this window, each needing an interactive session from Chris. A lapsed
token costs all read access — no §4.5 reconciliation, no §3.6 check, no ability
to close. Resting GTC stops still function; they live at Schwab. Only account
ACCOUNT_REDACTED is in the token's scope. Keep it that way on every re-auth.
