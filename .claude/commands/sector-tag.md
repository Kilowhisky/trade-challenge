---
description: Weekly sector tagger — classify the sweep's qualified universe into the three scout sectors (research/sectors.tsv). The right-hand side of the cohort join.
---

# /sector-tag — classify the qualified universe into the scout's sectors

The information-edge scout works an **earnings cohort**: qualified names whose
estimated next print falls in the option entry window. `mcp__engine__cohort`
builds it by joining the qualified universe (written by the weekly sweep)
against the sector tags — and only names tagged with one of the three
in-scope sectors survive the join. **Nothing wrote a sector tag before
2026-09-05.** Every scout pass from 2026-09-01 reported `cohort 0`, not
because nothing reports in the window, but because the join had an empty
right-hand side.

This pass is that writer. It is **read-only at the broker by construction** —
no account tools, no order tools, one Schwab-backed call (`get_datetime`) —
and it writes exactly one thing, through exactly one tool.

Design: `docs/superpowers/specs/2026-08-30-information-edge-scout-design.md`
§3.1 — *"Schwab exposes no sector field, so classification is done by model
over well-known tickers."*

## §Dispatch — scheduled, 09:40 ET on Saturday

The engine runs this at **09:40 ET on Saturday**, two hours behind the 07:40
whole-market sweep that populates the universe it reads (a sweep takes ~20
minutes). Agent: `.claude/agents/sector-tagger.md`.

## §A — Preconditions

1. `mcp__engine__get_datetime` for the Eastern date. Never the machine clock.
2. **`mcp__engine__universe_names_page(offset=0, limit=1).total` is 0** → emit
   the return line with `names 0` and stop. The sweep has not run; there is
   nothing to classify and nothing to invent.
3. Read the current tags via `mcp__engine__sectors_read()` if any exist
   (`symbol`, `sector`, `date`). Tags carry forward; this pass **adds and
   corrects**, it does not start from zero every week.

## §B — Classify

Read the qualified universe in pages via `mcp__engine__universe_names_page(
offset, limit)` at the same ~400-row page size used before (the universe is
~3,000 rows). For each row decide whether the company belongs to one of the
three sectors. **The sectors are a closed set**, and the writer refuses
anything else:

| Tag | Belongs | Does not belong |
|---|---|---|
| `consumer-software` | consumer software, streaming, apps, games, marketplaces, social, consumer fintech and subscription platforms | enterprise/B2B software, IT services, payment networks |
| `airlines-transport` | airlines, travel booking, cruise, hotels/lodging, rail, trucking, logistics, ride-hail, rental cars, aircraft lessors | auto manufacturers, defense aerospace, shipping-container lessors |
| `semis-hardware` | semiconductors, semi equipment, memory, networking and compute hardware, consumer electronics, data-center infrastructure, optical/interconnect | pure software, telecom carriers, utilities |

Everything else is **out of scope and is not tagged** — not as `other`, not at
all. `mcp__engine__cohort` skips untagged names exactly as it skips `other`,
and 3,000 rows tagged `other` is noise the next tagger has to read past. Use
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

All writes go through `mcp__engine__sector_write(rows=[{symbol, sector}, ...],
date=<YYYY-MM-DD>)`. Batches of **≤200 rows**. Every row is validated before
any row is written, so a refused batch has changed nothing: fix the row and
resend the batch.

Write only what is **new or changed**. Re-sending every existing tag is
harmless (the tool updates in place) but wastes the budget.

Never write sector tags any other way. Never write anything else.

## §D — Verify and return

1. `mcp__engine__cohort(date=<YYYY-MM-DD>)` — the cohort the scout will see on
   Tuesday. A non-empty result is the point of this job; an empty one is
   legitimate between seasons but must be reported as a number, never assumed.
2. Return exactly one JSON object matching the `SectorVerdict` schema:
   `names`, `tagged`, `new`, `retired`, `cohort`, and `summary` in the form:

```
SECTORS 2026-09-05 | names 3196 | tagged 412 (+37 new, 2 retired) | cohort 9 | -
```

`names` is the total from `universe_names_page`; `tagged` is the in-scope row
count after the write; `new` and `retired` come back **from
`mcp__engine__sector_write` itself**, not a count kept by hand; `cohort` is
the row count from `mcp__engine__cohort`. The trailing field in `summary` is
`-` or a one-clause note (e.g. `late sweep: qualified universe dated last
week`).

The scheduler relays the summary line to Discord. Chris reads it once a week;
make it true.
