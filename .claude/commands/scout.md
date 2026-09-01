---
description: One information-edge scout pass over the active earnings cohort. The unit of the research loop.
argument-hint: "[symbol to force into this pass, optional]"
---

# /scout — one pass of the information-edge loop

A scout pass samples non-mainstream sources for a few companies, records what
it finds in a dated per-name ledger, and escalates only where independent
evidence has accumulated. It answers one question: *has anything become
knowable about this company that the market has not yet priced?*

A scout pass never trades, never sizes a position, and never decides what a
finding means. Escalation hands the evidence to Chris; the thesis is his.

## §A — Preconditions

1. **`ALERT.md` exists and is unacknowledged** → the account is in
   closing-only posture. Research still runs (knowing things is free), but
   mark the return line `CLOSING-ONLY` so no escalation reads as actionable.
2. **The cohort is empty** → that is a correct result between earnings
   seasons, not a failure. Emit the return line with `cohort 0` and stop.
   Do not widen the window to find work.

## §B — The pass

### B1. Clock

`get_datetime` for the Eastern date. Never the machine clock — the laptop runs
Pacific and would file an evening pass under the wrong day, corrupting the
ledger dates that every later delta is computed against.

### B2. Cohort

```
scripts/cohort.sh <YYYY-MM-DD>
```

Prints `symbol	sector	est_next_earnings	days_out` for names whose estimated
print falls in the entry window. Take the **first 2–3** rows not already
observed today; the rest belong to later passes. Working the whole cohort in
one pass is how a scout runs out of budget and starts guessing.

The window is the option **entry** window, not an arbitrary lookahead: implied
volatility ramps in the final ~2 weeks into a print, so a name is researched
at exactly the point where a position could still be opened before the ramp.

### B3. Read the name's history FIRST

```
research/evidence/<SYMBOL>.jsonl
```

Read it before searching. This is what makes a delta possible, and the delta
is the whole signal. Note what the last pass found, how long ago, and what the
baseline looked like. If the file does not exist, this is the name's first
pass — record that in your reasoning, escalate nothing on it, and let the
baseline establish itself. **A first observation is never a delta.**

### B4. Sample the sources

For each name, search deliberately across source *types*, not just volume:

| Type | Where to look |
|---|---|
| `end-user` | app store reviews, subreddits, review platforms, forums |
| `employee` | employer-review trends, job postings, role changes |
| `counterparty` | supplier and customer announcements, contracts, trade press |
| `enthusiast` | teardowns, benchmarks, specialist blogs |
| `primary-doc` | filings, patents, regulatory dockets |
| `mainstream` | **the kill switch — search it explicitly** |

Measured constraints on what actually works here, from a 2026-08-30 probe:

- **WebSearch is the workhorse.** It reaches Reddit threads, review platforms
  and specialist press well.
- **Direct article fetch is unreliable.** Major publisher pages often return
  navigation furniture with the body truncated. When extraction fails, record
  that it failed rather than silently dropping the source.
- **Employer-review sites block direct fetch** (403). The `employee` type is
  the weakest of the six; lean on job postings and search summaries, and never
  require it for a bar clear.
- **Aggregator sites are pointers of unknown provenance,** possibly
  model-generated. Cite the underlying platform, never the aggregator alone.

### B5. Record every observation

```
scripts/evidence-append.sh SYMBOL <YYYY-MM-DD> '<json>'
```

Required: `claim`, `url`, `source_type`, `observed`, `independence`.

`claim` must be **specific and falsifiable** — something that can later be
scored right or wrong. `independence` is your stated reason this source is not
a restatement of another one you recorded; "separate user reports, not derived
from the press release above" is a reason, "different website" is not.

Record what you found even when it clears nothing. The ledger's value is
longitudinal: a null result this quarter is what makes next quarter's change
visible.

## §C — The escalation test

Escalate **only** when all four hold:

1. **2+ distinct source types** among today's and prior observations — types,
   not URLs.
2. **A specific falsifiable claim**, not a sentiment impression.
3. **No mainstream coverage.** If it is there, it is priced. Dead.
4. **A plausible link to a financial line item** — revenue, margin, subscriber
   count, guidance. "Users are annoyed" is not a link; "churn in the segment
   that is 40% of revenue" is.

Accumulating corroboration sharpens the *ranking* of what you surface. It is
not a fourth way to clear the bar.

On a clear:

```
scripts/escalation-log.sh raise SYMBOL <YYYY-MM-DD> '<json>'
```

with `claim`, `direction` (`up`|`down`), `event_date`, and `source_types`.
Recording the prediction **before** the outcome is the entire point — it is
what makes a hit rate exist rather than a story told afterward. The writer
refuses a raise carrying an outcome, and refuses fewer than two distinct valid
types.

## §D — Return

One line, plus one `ESCALATE:` line per clear. The wrapper relays escalations
to Discord; a clean pass relays nothing, because a scout that reports daily
that it found nothing trains its reader to stop looking.

```
SCOUT 2026-09-01 | cohort 14 | observed 3 | escalated 0 | -
```

## §E — What this pass must not do

- **Not form a thesis.** Assemble evidence and stop. The judgement is Chris's,
  and it rests on domain knowledge you do not have.
- **Not size or price a position.** Even on an escalation.
- **Not lower the bar to produce output.** Zero escalations is the expected
  result of almost every pass. The strategy's own history is two trades in
  several years, and a scout that escalates weekly is not finding more, it is
  finding worse.
- **Not trade.** You hold no order tools; this is enforced by the harness, and
  restated here so the reason is on the record rather than only in the
  frontmatter.
