# Trading Competition

A one-month, cash-only trading competition run by an AI agent under a written
rule set. This repo is the system of record: the rules, the strategy, the trade
log, and the audit trail.

## Capital and scoring

The account holds **$1,800.00**, split deliberately:

| | |
|---|---|
| **Competition capital** | $900.00 — the money actually at risk |
| **Settlement reserve** | $900.00 — float to bridge T+1 settlement, never deployed |
| **Score** | Account value at the final closing bell **minus the $900.00 reserve** |

Every risk limit in the manual — position caps, sleeve ceilings, the high-water
mark, the drawdown circuit breaker — is computed against **competition capital**,
never against total account value. The reserve adds float, never risk. Total
position exposure never exceeds 100% of competition capital.

Marks are honest by construction: the liquidity floors mean scoring prices are
realizable prices. Nothing is liquidated for scoring.

## What's here

| File | Purpose |
|---|---|
| `CLAUDE.md` | **The operating manual** — the binding rule set. Loads automatically every Claude Code session. |
| `docs/superpowers/specs/2026-08-13-competition-strategy-design.md` | **The playbook** — how the box is actually traded: sleeve architecture, selection rules, order workflow, endgame calendar. Required reading at every session open. |
| `docs/superpowers/specs/2026-08-13-adversarial-review.md` | Four red-team agents attacking the strategy; every finding and its disposition. |
| `docs/superpowers/specs/2026-08-13-comparative-research.md` | Five research agents on what actually works in AI/small-account trading; the 12 tightenings adopted from it. |
| `docs/2026-08-14-day-one-candidates.md` | The core-sleeve screen run the evening before the window opened. Candidates, not decisions. |
| `trade-log.csv` | Every order, appended and committed in the session it was placed. |
| `status/` | Daily close-of-session written status notes. |
| `ALERT.md` | Created only when something needs the account owner. An unacknowledged alert forces closing-only posture at the next session open. |

The manual and the playbook are not interchangeable. **Where they disagree, the
manual wins** — and the disagreement is a defect to fix, not a choice to make.
Rules in the playbook that are stricter than the manual are marked
*(strategy rule)*: discretionary, changeable without an amendment.

## Working rhythm

1. Open a Claude Code session in this directory.
2. `CLAUDE.md` loads automatically. Read the playbook before acting.
3. Reconcile against the broker before anything else — never begin from an
   assumed state.
4. Trade within the rules; every order gets logged before it is placed.
5. At session close: write a status note, commit the log.

## Why git

The commit history is timestamped and hard to quietly revise. At the end of the
month, "here's the log" is a stronger claim than a spreadsheet that could have
been edited after the fact.

**One disclosed exception.** Before this repo was published, its history was
rewritten to remove things that should never have been in it: the brokerage
account number and Schwab API account hash (now `ACCOUNT_REDACTED` and
`HASH_REDACTED`), a reference to unrelated holdings in a personal account, and
the author's email address. No dollar figure, trade, rule, rationale, or
timestamp was altered. All of that was done before publication — nothing after
it is revised.

## The four things to remember

1. **The agent does not run continuously** — only while a session is open. Every
   stop must be a resting order at Schwab, never something the assistant intends
   to watch for. See §4.3.
2. **Stops do not cover gaps.** They trigger only in regular-session trading. A
   10% stop can fill far below 10% down on overnight news. See §0 and §3.7.
3. **Long options are never held near expiration.** Auto-exercise of an
   in-the-money contract would blow through a cash account this size. See §3.3.
4. **Every percentage is against competition capital**, not account value.
   Reading a cap against $1,800 doubles the intended risk.
