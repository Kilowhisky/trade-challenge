---
description: Weekly sector tagger — classify the sweep's qualified universe into the three scout sectors (research/sectors.tsv). The right-hand side of the cohort join.
---

# /sector-tag — classify the qualified universe into the scout's sectors

The information-edge scout works an **earnings cohort**: qualified names whose
estimated next print falls in the option entry window. `cohort.sh` builds it by
joining `research/universe-qualified.tsv` (written by the weekly sweep) against
`research/sectors.tsv` — and only names tagged with one of the three in-scope
sectors survive the join. **Nothing wrote `sectors.tsv` before 2026-09-05.**
Every scout pass from 2026-09-01 reported `cohort 0`, not because nothing
reports in the window, but because the join had an empty right-hand side.

This pass is that writer. It is **read-only at the broker by construction** —
no account tools, no order tools, one Schwab call (`get_datetime`) — and it
writes exactly one file, through exactly one script.

Design: `docs/superpowers/specs/2026-08-30-information-edge-scout-design.md`
§3.1 — *"Schwab exposes no sector field, so classification is done by model
over well-known tickers."*

## §Dispatch — scheduled, Saturday 09:40 ET

`docker/crontab` runs this at **09:40 ET on Saturday**, two hours behind the
07:40 whole-market sweep that writes the two files it reads (a sweep takes
~20 minutes). Sunday is allowed for a forced catch-up, like the sweep. Agent:
`.claude/agents/sector-tagger.md`.

## §A — Preconditions

1. `get_datetime` for the Eastern date. Never the machine clock.
2. **Both inputs must exist**: `research/universe-qualified.tsv` (ten columns,
   the qualified set) and `research/universe-names.tsv` (`symbol`,
   `description` — the same rows, same order). If either is missing, emit the
   return line with `names 0` and stop. The sweep has not run, or ran before
   2026-09-05 without emitting them; there is nothing to classify and nothing
   to invent.
3. Read the current `research/sectors.tsv` if it exists (`symbol`, `sector`,
   `date`). Tags carry forward; this pass **adds and corrects**, it does not
   start from zero every week.

## §B — Classify

Read `research/universe-names.tsv` in chunks (`Read` with `offset`/`limit`,
~400 lines at a time — the file is ~3,000 rows). For each row decide whether
the company belongs to one of the three sectors. **The sectors are a closed
set**, and the writer refuses anything else:

| Tag | Belongs | Does not belong |
|---|---|---|
| `consumer-software` | consumer software, streaming, apps, games, marketplaces, social, consumer fintech and subscription platforms | enterprise/B2B software, IT services, payment networks |
| `airlines-transport` | airlines, travel booking, cruise, hotels/lodging, rail, trucking, logistics, ride-hail, rental cars, aircraft lessors | auto manufacturers, defense aerospace, shipping-container lessors |
| `semis-hardware` | semiconductors, semi equipment, memory, networking and compute hardware, consumer electronics, data-center infrastructure, optical/interconnect | pure software, telecom carriers, utilities |

Everything else is **out of scope and is not tagged** — not as `other`, not at
all. `cohort.sh` skips untagged names exactly as it skips `other`, and a
3,000-row file of `other` is noise the next tagger has to read past. Use
`other` for one purpose only: a name **previously tagged in-scope** that on
review does not belong. That is how a bad tag is retired without deleting a
row by hand.

Rules of judgement:
- Classify from the description and your knowledge of the company. A
  description that is a bare ticker or a fund name (ETF, trust, fund, index)
  is never in scope — the scout works single names.
- When genuinely unsure whether a company belongs, **leave it untagged.** A
  missing name costs one quarter's observation; a wrong tag spends scout
  budget on a name Chris has no feel for, and the edge being traded is his
  domain knowledge (catalyst.md §B2). `WebSearch` is permitted for a handful
  of unfamiliar names, not as a per-row lookup.
- A name already tagged keeps its tag unless you have a specific reason to
  change it. Week-over-week churn is the open question in the design (§8.2);
  do not manufacture it.

## §C — Write

All writes go through `scripts/sector-write.sh --batch DATE`, as a heredoc on
the bare script path, `SYMBOL SECTOR` per line — the one multi-line form the
container's permission gate accepts. Batches of **≤200 lines**. Every line is
validated before any row is written, so a refused batch (exit 2 names the bad
line) has changed nothing: fix the line and resend the batch.

```
scripts/sector-write.sh --batch 2026-09-05 <<'EOF'
UAL airlines-transport
NFLX consumer-software
AMAT semis-hardware
EOF
```

Write only what is **new or changed**. Re-sending every existing tag is
harmless (the writer updates in place) but wastes the budget.

Never write `sectors.tsv` any other way. Never write any other file.

## §D — Verify and return

1. `scripts/cohort.sh DATE` — print the cohort the scout will see on Tuesday.
   A non-empty output is the point of this job; an empty one is legitimate
   between seasons but must be reported as a number, never assumed.
2. Return **one line**, nothing else:

```
SECTORS 2026-09-05 | names 3196 | tagged 412 (+37 new, 2 retired) | cohort 9 | -
```

`names` is the row count of `universe-names.tsv`; `tagged` is the in-scope
row count of `sectors.tsv` after the write; `new` and `retired` are this
pass's additions and `other` demotions; `cohort` is the line count from
`cohort.sh`. The trailing field is `-` or a one-clause note (e.g. `late sweep:
qualified file dated last week`). `FAIL: <reason>` if a batch could not be
written after one retry.

The scheduler relays the line to Discord. Chris reads it once a week; make it
true.
