# deep-research agent — RETIRED (trading desk, 2026-09-27)

The pre-open and post-close deep runs are replaced by the trading desk's
evening analyst chain and pre-open pass, and by engine-computed briefings and
scoring (engine/tc/desk/briefing.py, scoring.py). The desk: four analysts file
scored pitches through typed engine tools and a portfolio manager makes the
calls (`engine/tc/desk/`; jobs `desk_evening`, `desk_preopen`, `pm` in
`engine/tc/jobs/spec.py`).

Do not re-create this file.
