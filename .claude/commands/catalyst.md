---
description: One catalyst sweep of the in-scope sectors for non-calendar stories. The off-season half of the research loop.
argument-hint: "[theme or symbol to force into this pass, optional]"
---

# /catalyst — one sweep for non-calendar catalysts

A catalyst pass sweeps the three in-scope sectors for stories that are not
anchored to an earnings date: merger and acquisition chatter, product launches
and their reception, supply and hedging agreements, sustained outages,
regulatory action. It records what it finds in the same dated per-name ledger
the scout uses, and escalates only where independent evidence has accumulated.

This is the sibling of `/scout`, and the difference is the driver: **the scout
starts from the earnings calendar, this pass starts from the sources.** It is
also the only channel that produces anything between earnings seasons.

A catalyst pass never trades, never sizes a position, and never decides what a
finding means. Escalation hands the evidence to Chris; the thesis is his.

## §A — Preconditions

1. **`ALERT.md` exists and is unacknowledged** → closing-only posture. The
   sweep still runs — knowing things is free — but mark the return line
   `CLOSING-ONLY` so no escalation reads as actionable.
2. **No sector universe on disk yet** (`research/sectors.tsv` absent) → emit
   the return line with `scanned 0` and stop. The weekly sweep populates it;
   an empty universe is a correct state, not a failure to work around.

## §B — The sweep

### B1. Clock

`get_datetime` for the Eastern date. Never the machine clock — the laptop runs
Pacific and would file an evening pass under the wrong day, corrupting the
ledger dates every later delta is computed against.

### B2. Scope

The in-scope sectors are the three in `research/sectors.tsv`:
`consumer-software`, `airlines-transport`, `semis-hardware`. Names tagged
`other` are out of scope — not because nothing happens there, but because the
edge being traded is Chris's own domain knowledge, and a well-corroborated
story about a company he has no feel for is a stranger's tip rather than an
edge.

You are not iterating a list. Search the *sectors* for developing stories, then
map what you find back to a tagged symbol. A story about an untagged company is
worth recording only if the company plausibly belongs to one of the three — in
which case tag it via `scripts/sector-write.sh` and proceed.

### B3. What to look for

| Catalyst | What makes it tradeable rather than noise |
|---|---|
| Merger / acquisition chatter | A specific counterparty, price, or process detail — not "exploring options" |
| Product launch or reception | Measurable reception (review trends, refund/return chatter, retention), not launch-day coverage |
| Supply / hedging agreements | A term, a duration, a counterparty — the airline fuel-hedge case is the model |
| Outage or defect | Sustained and segment-specific, with an identifiable revenue line behind it |
| Regulatory action | A docket or filing that exists, not an anticipated one |

**Rumour is abundant and nearly all of it is noise.** The discipline that makes
this channel useful is the independence test in §C, applied strictly. Merger
rumours in particular propagate by citation: a dozen articles routinely trace
to one unnamed source, which is one source.

### B4. Sample across source types

| Type | Where to look |
|---|---|
| `end-user` | subreddits, review platforms, forums, app stores |
| `employee` | job postings, role changes, employer-review trends |
| `counterparty` | supplier/customer announcements, contracts, trade press |
| `enthusiast` | teardowns, benchmarks, specialist blogs |
| `primary-doc` | filings, patents, regulatory dockets |
| `mainstream` | **the kill switch — search it explicitly** |

Measured constraints from a 2026-08-30 probe: WebSearch is the workhorse and
reaches Reddit, review platforms and specialist press well; direct article
fetch on major publishers is unreliable and often returns navigation furniture
— when extraction fails, record the failure rather than dropping the source
silently; employer-review sites block direct fetch (403), so `employee` is the
weakest type and must never be required for a bar clear; aggregator sites are
pointers of unknown provenance and possibly model-generated, so cite the
underlying platform, never the aggregator alone.

### B5. Record every observation

```
scripts/evidence-append.sh SYMBOL <YYYY-MM-DD> '<json>'
```

Required: `claim`, `url`, `source_type`, `observed`, `independence`.

`claim` must be specific and falsifiable. `independence` is your stated reason
this source is not a restatement of another — "separate supplier disclosure,
not derived from the wire story above" is a reason; "different website" is not.

Record what you found even when it clears nothing. A null result this month is
what makes next month's change visible.

## §C — The escalation test

Escalate **only** when all four hold:

1. **2+ distinct source types** — types, not URLs, and not two citations of one
   origin.
2. **A specific falsifiable claim.**
3. **No mainstream coverage.** If it is there, it is priced. Dead.
4. **A plausible link to a financial line item.**

On a clear:

```
scripts/escalation-log.sh raise SYMBOL <YYYY-MM-DD> '<json>'
```

with `claim`, `direction` (`up`|`down`), `event_date`, and `source_types`.
`event_date` is when you expect the market to learn — often the next scheduled
print, sometimes a deal deadline or a launch date. Recording the prediction
**before** the outcome is the entire point: it is what makes a hit rate exist
rather than a story told afterward.

## §D — Return

One line, plus one `ESCALATE:` line per clear. The wrapper relays escalations
to Discord; a clean pass relays nothing.

```
CATALYST 2026-09-01 | scanned 62 | observed 4 | escalated 0 | -
```

## §E — What this pass must not do

- **Not form a thesis.** Assemble evidence and stop.
- **Not size or price a position.**
- **Not treat rumour volume as corroboration.** Ten articles from one unnamed
  source is one source, and this is the failure mode that would make this
  channel worse than useless.
- **Not lower the bar to produce output.** Zero escalations is the expected
  result of almost every pass.
- **Not trade.** No order tools, enforced by the harness.
