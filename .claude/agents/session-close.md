# session-close agent — RETIRED (v3 Plan 0c, 2026-09-08)

The §7.2 close is engine code: `engine/tc/loops/session.py` writes the daily
session-status row from a live post-bell broker read and performs the one
irreversible number this agent guarded — the high-water mark, which ratchets
only here, only up, and only from a CLOSE, never adopted from an intraday
high. The module also carries the pre-2026-08-31 basis conversion this file
specified (`legacy_hwm_to_account_basis`), applied once when the mark is
seeded. It is scheduled as `session_close` in `config.yml`, at the same slot
this agent ran before, ahead of the postclose deep run that reads its output.

Read `CHANGELOG.md` and
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` for why.
Do not re-create this file.
