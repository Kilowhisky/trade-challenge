# tick-watch agent — RETIRED (v3 Plan 0c, 2026-09-08)

The monitoring loop is engine code: `engine/tc/loops/tick.py` runs the seven
watches this agent swept — restriction before naked, drawdown on account
value, and the rest — and appends one ledger row per sweep, with `BLIND` as a
state rather than a crash when the broker token is dead.
`engine/tc/loops/clocks.py` carries the §3.3 option-expiry and §3.5
leveraged-hold clocks as arithmetic on the ledger, not a model reading a
calendar. The sweep is scheduled as `tick` in `config.yml`, on the same
cadence this agent ran before. Escalation and notification are decided by the
job that calls the sweep, never inside it — the tick path places nothing,
exactly as this agent required.

Read `CHANGELOG.md` and
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` for why.
Do not re-create this file.
