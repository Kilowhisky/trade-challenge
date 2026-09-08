# /weekly-universe — RETIRED (v3 Plan 0c, 2026-09-08)

The weekly whole-market sweep is engine code: `engine/tc/loops/universe.py`,
scheduled in `config.yml` as `weekly_universe` at 07:40 ET on Saturdays. It
fetches the Nasdaq Trader directory over HTTP, applies the same five gates
against `rules.yml`, writes the `universe` table and renders `universe.md`
through the same document validator this command used to satisfy. Nothing about
the sweep required judgement, and the elaborate machinery this file described —
150-symbol chunks chosen to force the harness to spill a payload to disk, a
filter script the agent was forbidden to feed by hand, a flag whose only job was
stopping the daily run from overwriting the weekly file — existed to keep a
model away from data it did not need. There is no model in that loop now.

Read `CHANGELOG.md` and
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` for why.
Do not re-create this file.
