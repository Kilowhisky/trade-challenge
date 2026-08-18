# Trading Competition — Operating Manual

**Account owner:** Chris
**Starting capital:** $900.00 competition capital + $900.00 settlement reserve
  (amended 2026-08-13, pre-window, field-agreed — see §9 note below)
**Capital amendment 2026-08-14 (mid-window, field-agreed):** **+$2,000.00**
  transferred in after the day-one close, verified settled at the broker
  ($3,799.38 liquidation, $3,402.48 settled cash, $0 unsettled/pending).
  Competition capital ≈ **$2,899.38** at amendment. Every competitor made the
  same addition (field-agreed), so the unchanged scoring formula stays
  comparable. The §1.7 transfer prohibition was ratified as a **one-time
  exception for this transfer only** and remains in force for the remainder
  of the window. Chris's authorizing message is quoted verbatim in the
  amendment commit per §9.
**Scoring:** final account value **minus the $900.00 reserve** (formula
  unchanged by the 2026-08-14 amendment; the field agreed rankings remain
  comparable because every account added the same $2,000)
**Risk anchoring:** every percentage rule in §3 — position caps, sleeve limits,
  high-water mark, §3.6 triggers — is computed against **competition capital =
  account value − $900.00 reserve**, never against total account value. Caution
  and Halt are **$2,552.00 / $2,320.00** of competition value (−12% / −20%
  from the re-anchored $2,900.00 high-water mark; before the 2026-08-14
  amendment: $792.00 / $720.00 from $900.00). The reserve exists
  only to bridge T+1 settlement; total position exposure never exceeds 100% of
  competition capital. The reserve adds float, never risk.
**Competition window:** 2026-08-14 → 2026-09-14 (one calendar month)
**Final NYSE session:** Monday 2026-09-14 · 21 sessions total (Labor Day 2026-09-07 excluded)
**§8 lockout — no new positions, all options closed, from:** Thursday 2026-09-10
**Account:** ACCOUNT_REDACTED "LLM YOLO" · CASH · hash `HASH_REDACTED`
**Initial high-water mark:** $900.00 · re-anchored **$2,900.00** on 2026-08-14
  (round anchor chosen by Chris — absorbs day one's −$0.62 into the baseline)
  · Caution $2,552.00 · Halt $2,320.00
**Broker:** Charles Schwab, accessed via the Schwab MCP server
**Account type:** Cash account (no margin)
**Manual version:** v3

This file is the binding rule set for this project. It loads automatically at the
start of every session. Read it before taking any action on the account.

**This file is only half the system.** It defines the *box* — what may never be
done. It does not define *how the account is traded*. That is the playbook:

> **`docs/superpowers/specs/2026-08-13-competition-strategy-design.md`**
> — sleeve architecture and the reserve invariant, core and catalyst selection
> rules, the options sleeve, the order workflow, the monitoring loop, the
> session protocol, and the endgame calendar. Two supporting documents in the
> same directory record a four-agent adversarial review and a five-agent
> comparative research synthesis with the twelve tightenings adopted from it.

**Read the playbook at every session open, immediately after §4.5
reconciliation, and before any order.** It does not load automatically and a
session that skips it will trade the box instead of the strategy — inside the
rules, but not to the plan. Where the playbook and this manual disagree, **this
manual wins**, and the disagreement is a defect to fix rather than a choice to
make. Rules in the playbook marked *(strategy rule)* are stricter than this
manual: discretionary, and changeable without a §9 amendment.

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
   and cash-sweep interest are permitted inflows and count toward scoring.
   *(One-time exception, ratified 2026-08-14 per §9: a single field-agreed
   +$2,000.00 deposit after the day-one close — every competitor made the
   same addition. The prohibition is otherwise unchanged and no further
   transfers are permitted.)*

---

## 2. Permitted instruments

The organizing principle: **buying is permitted, selling obligations is not.**
Buying an option caps the loss at the premium paid. Selling one creates an
obligation that can outrun the account.

| Instrument | Permitted | Constraints |
|---|---|---|
| Listed common stocks | Yes | ≥ $5/share, ≥ 1M avg daily volume, major exchange |
| ETFs | Yes | Same liquidity floor |
| Leveraged / inverse ETFs | Yes | 20% aggregate cap, max 5 trading day hold (§3.5) |
| Long calls (buying) | Yes | Quality floors in §3.2 |
| Long puts (buying) | Yes | Quality floors in §3.2 |
| **Selling any option** | **No** | See §2.1 |
| Spreads | **No** | §1.3 |

**2.1 — Why no option selling at all, including "safe" covered calls.**

Three independent reasons, any one of which is sufficient:

- **It's arithmetically impossible here.** A covered call needs 100 shares. At the
  §1.4 floor of $5/share that's $500 minimum — but §3.1 caps a single position at
  35% of $900 = $315. No stock satisfies both rules. Same for a cash-secured put:
  a $5 strike ties up $500 of collateral, well past the cap. *(2026-08-14 note:
  the capital amendment raised the §3.1 cap to ≈$1,015, so this leg alone no
  longer blocks sub-$10 underlyings — but the other two reasons each remain
  sufficient, and §1.2 is unamendable core. The prohibition stands unchanged.)*
- **Sizing a short option on premium received is a trap.** A sold put collects
  maybe $40 while committing $500+ of collateral to a gap-down. Any premium-based
  cap waves it straight through.
- **A covered call can become a naked call by accident.** Hold shares, sell a call
  against them, then the shares get taken out by a stop or a manual exit — what's
  left is an uncovered short call with unlimited risk, created without a single
  rule violation. Removing the whole category removes the path.

**Note on the original rule set:** the competition began with "no puts," written
believing puts carry the unlimited risk. They do not — a *long* put has the same
capped-at-premium risk as a long call. The unlimited position is the *uncovered
short call*. Corrected: long puts permitted, all option selling prohibited.

---

## 3. Position sizing and risk limits

**3.1 — Single position cap.** No single position may exceed **35% of
competition capital (account value − the $900 reserve) after the order
fills**, counting all prior adds to that position. This is
a cap on the resulting total, not on each individual order — three compliant 30%
buys stacking into a 90% position is a violation, not a loophole.

**3.2 — Option sizing and quality.** Options are capped as a percentage of
**competition capital**, so capacity scales with the account — wins expand it,
losses shrink it — and gated on contract quality. *(Amended 2026-08-13 per §9:
was fixed dollar caps $135/$180 plus a $360 non-replenishing monthly
cumulative cap; the cumulative cap is deleted.)*

- Max premium in any single option position: **20% of competition capital at
  entry** (≈$580 at $2,899; was 15%/≈$435 before the 2026-08-17 §9
  amendment, $135 at $900).
- Max total open option premium: **30% of competition capital** (≈$870 at
  $2,899; was 20%/≈$580 before the 2026-08-17 §9 amendment, $180 at $900).
  *(Amended 2026-08-17 per §9, Chris-initiated post-close, book flat: caps
  raised 15%→20% per position, 20%→30% aggregate. Chris's words quoted in
  the amendment commit. All quality floors, §3.3 expiration handling, §3.7,
  and the 9/4 all-flat strategy rule unchanged.)*
- No limit on the number of open option positions. *(Amended 2026-08-13 per
  §9; was max 2. The 20% aggregate premium cap is the binding constraint;
  every position still carries its own §3.3 expiration clock and §3.2
  quality floors.)*
- Cumulative premium spent is **logged** every trade (§7.2) but no longer
  capped.

Quality floors — all must hold at entry:
- **≥ 21 days to expiration**
- **Delta ≥ 0.35** (no far-OTM lottery tickets)
- **Open interest ≥ 500** on the specific contract
- **Bid/ask spread ≤ 10% of mid**
- Underlying satisfies §1.4 and the §2 liquidity floor

**3.3 — Expiration handling. This is the single largest event risk in the
account.** The OCC auto-exercises any long option that finishes $0.01 in the
money. On a $900 cash account that is catastrophic:
- An ITM long call auto-exercises into a strike × 100 stock purchase — $2,000+
  the account cannot pay — producing a cash call, forced liquidation, and a
  violation under §5.
- An ITM long put exercises into a **short stock position**, which §1.5 forbids
  and a cash account cannot hold, forcing a broker buy-in at any price.

**Rules:** close every long option when it reaches **5 days to expiration**, and
under no circumstances hold any option into its expiration week's final trading
day. If a position cannot be closed for any reason, file a **Do-Not-Exercise
instruction with Schwab the same session** and notify Chris immediately.

**3.4 — Stop-loss.** Every stock and ETF position gets a **stop-limit** order
with the trigger **10% below entry** and the limit **5% below the trigger**,
placed as a resting GTC order immediately after the entry fill is confirmed.
The trigger level is a **floor**: it may be raised, never lowered.
*(Amended 2026-08-13 per §9.)*
Long options do not get stops — option stops fill terribly on wide spreads — and
are controlled by §3.2 sizing instead.

If a stop-limit is **gapped through and left unfilled**, the position is closed
at market at the next session open. This is the one permitted use of a market
order (§4.1).

**3.5 — Leveraged ETF constraints.** Leveraged and inverse ETFs reset daily and
decay when held through volatility.
- **20% of competition capital, aggregate** across all leveraged/inverse ETFs
  combined — not per position. A 3x fund at 20% is already 60% effective
  notional. *(Amended 2026-08-13 per §9: was "of account", 2× looser on the
  $1,800 balance than the header's competition-capital anchoring intends.)*
- Max holding period **5 trading days** (NYSE sessions, holidays excluded). A
  position still open on day five gets closed, and this survives §3.6.
- These limits **also apply to options on leveraged ETFs**, which are otherwise
  neither "a leveraged ETF position" nor covered by the hold limit.

**3.6 — Drawdown circuit breaker.** Measured against the **high-water mark**
(the highest **competition-capital** value — account value − the $900
reserve — recorded to date). **Checked as the first action of every
session**, not only at close.

| Level | Trigger | Action |
|---|---|---|
| **Caution** | −12% from high-water (**$2,552** from $2,900) | Halve all new position sizes. No new option positions. Close any option position at a loss. |
| **Halt** | −20% from high-water (**$2,320** from $2,900) | **No buy orders of any kind.** Notify Chris. Trading resumes only after an explicit conversation. |

"New position" means **any buy order** — including adds to an existing position,
re-entering a name that just stopped out, and rolling an option. Closing orders
are always permitted at any drawdown level, and §3.5's forced close still applies.

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

**3.8 — Correlation cap.** Max **50% of competition capital** in positions with
materially correlated exposure — same sector, same macro theme, or
correlation > 0.7. *(Amended 2026-08-13 per §9: was "of account".)*
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
begin from an assumed state. Then check §3.6. Then read `ALERT.md` if one exists
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
- **Order-rate ceilings.** Maximum **2 per symbol per session** and **3
  replaces per resting stop per day**. *(Amended 2026-08-13 per §9: the
  5-placed-orders-per-session ceiling is lifted — Chris, pre-window, calm
  conditions, no position open. The per-symbol and per-stop ceilings stand.)*
  A ceiling **trips on the attempt to exceed it** — placing the Nth order is
  legal and routine; the trip is the would-be (N+1)th. *(Ruled by Chris
  2026-08-13 per §9: "Trips on N+1".)*
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

**7.1 — Trade log.** Every order is appended to `trade-log.csv` and committed to
git in the session it was placed. The git history is the audit trail.

**7.2 — Daily status.** At each session close, write to `status/YYYY-MM-DD.md`:
positions held, settled and unsettled cash, account value, high-water mark,
current drawdown level, cumulative option premium spent to date, what changed and
why, and any rule that bound a decision.

**7.3 — Honest reporting.** Losses get reported the same way gains do, in the
same detail, without softening. A log that only produces good news is worthless —
and I am the one writing the log that constrains me, so this matters.

---

## 8. Scoring and endgame

- Winner determined by **final account value minus the $900.00 settlement
  reserve, at the closing bell on the final NYSE trading session on or before
  END_DATE** — per the header scoring line (amended 2026-08-13, pre-window,
  field-agreed). This bullet previously said "total account value" and
  predated the reserve; the header governs.
- Account value = settled cash + unsettled cash + marked-to-market positions.
  The score subtracts the reserve from that total.
- **No new positions may be opened in the final 3 trading days.** Marking to
  market at a fixed deadline otherwise makes maximum variance on the last morning
  the mathematically optimal play — 0DTE contracts into an earnings print. That
  is a bug in the scoring rule, not a strategy.
- **All option positions must be closed by the start of the final 3 trading
  days**, bringing open premium to $0.
- Log the final value and commit it as the last entry.

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

## Pre-competition checklist

Worked 2026-08-13. Evidence in `status/2026-08-13.md`.

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
