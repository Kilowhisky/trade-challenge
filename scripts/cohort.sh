#!/bin/bash
# cohort.sh — RETIRED (trading desk, 2026-09-27)
#
# This built the earnings cohort (qualified, sector-tagged names whose
# estimated next print fell in the scout entry window) for the retired v2
# scout/catalyst pipeline. That pipeline is gone; the trading desk
# (engine/tc/desk/) replaces it with analysts filing scored pitches through
# typed engine tools and a portfolio manager making the calls. The window
# keys this script read (rules.yml strategy.scout_entry_window_min_days /
# scout_entry_window_max_days) were retired and are guarded against
# returning by engine/tc/rules/consistency.py.
#
# Do not re-create this script's body. If cohort logic is needed again, it
# belongs in engine/tc/desk/, not here.

echo "cohort.sh: RETIRED (trading desk, 2026-09-27) — see engine/tc/desk/" >&2
exit 1
