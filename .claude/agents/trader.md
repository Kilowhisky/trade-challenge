# trader agent — RETIRED (v3 Plan 0c, 2026-09-08)

This agent is not ported. The order path becomes deterministic engine code in
`engine/tc/orders/` (Plan 1): sizing, the §4.9/§4.10 pre-trade checks, and
order placement move out of prose the model followed and into code the engine
runs. Claude never holds `place`, `cancel`, `replace` or `preview` — the
model's only write-shaped tools on the decide role are the `propose_*` family
(`propose_entry`, `propose_exit`, `propose_option_close`, `get_proposal`),
which the engine then decides on. That the model cannot reach an order tool
at all is a design decision, not an oversight, and a contract test enforces
it (spec §5, §10).

Read `CHANGELOG.md` and
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` for why.
Do not re-create this file.
