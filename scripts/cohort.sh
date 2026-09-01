#!/bin/bash
# cohort.sh — print the active earnings cohort: qualified, sector-tagged names
# whose ESTIMATED next print falls in the entry window.
#
# Usage: scripts/cohort.sh YYYY-MM-DD        (Eastern, from get_datetime)
#
# Output, tab-separated, sorted by days_out ascending:
#
#     symbol <TAB> sector <TAB> est_next_earnings <TAB> days_out
#
# Column 3 is the ESTIMATED NEXT print, not the last one — a reader handed
# last_earnings here would see a catalyst 91 days in the past and treat a live
# setup as already spent.
#
# The window is 21-42 days out because that is the option ENTRY window: IV
# ramps in the final ~2 weeks into a print, so the research window and the
# entry window are deliberately the same interval. Those two numbers are NOT
# written here — they are read from rules.yml, which owns every rule parameter
# in this repo exactly once. check-consistency.sh's hard-code check only greps
# for percentage arithmetic, so a literal `WIN_MIN=21` in this file would never
# be caught; it would simply drift away from the rule it claims to implement.
#
# An empty cohort is a CORRECT answer between earnings seasons, so this exits 0
# with no output rather than signalling failure. If it exited non-zero, the
# scheduled job would report failure every day from August to October and the
# deadman watchdog would cry wolf until nobody read it. The same goes for the
# input files not existing yet, before the first weekly sweep has run.
#
# Date arithmetic is done in awk, never by shelling out to `date -d`: that flag
# is GNU-only and this runs on BSD (macOS laptop) as well as GNU (the server).
set -euo pipefail

[ "$#" -eq 1 ] || { echo "cohort: expected YYYY-MM-DD, got $#" >&2; exit 2; }
today="$1"
case "$today" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "cohort: bad date '$today'" >&2; exit 2 ;;
esac
# Range-check the parts too. A malformed today shifts the whole cohort by
# however far it is wrong, and nothing downstream can tell that it happened.
# 10# forces base 10: bash reads a leading-zero month as octal otherwise.
if [ "$((10#${today:5:2}))" -lt 1 ] || [ "$((10#${today:5:2}))" -gt 12 ] \
   || [ "$((10#${today:8:2}))" -lt 1 ] || [ "$((10#${today:8:2}))" -gt 31 ]; then
  echo "cohort: bad date '$today'" >&2; exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/lib-rules.sh"
load_rules || exit 7
WIN_MIN="$(rule_get strategy_scout_entry_window_min_days)" || exit 7
WIN_MAX="$(rule_get strategy_scout_entry_window_max_days)" || exit 7

# Days between prints. This is a calendar constant — one quarter — not a risk
# parameter, and rules.yml carries no key for it.
QUARTER=91

dir="${TC_RESEARCH_DIR:-$here/../research}"
uq="$dir/universe-qualified.tsv"; st="$dir/sectors.tsv"
[ -f "$uq" ] && [ -f "$st" ] || exit 0

tab="$(printf '\t')"

awk -F'\t' -v today="$today" -v wmin="$WIN_MIN" -v wmax="$WIN_MAX" \
           -v q="$QUARTER" -v stf="$st" '
  BEGIN { BAD = -999999999 }

  # Days since the Unix epoch, and its inverse. Howard Hinnant s civil
  # algorithms; both verified against an independent calendar over every date
  # from 1969-01-01 to 2031-12-31.
  function g(y,m,d,  era,yoe,doy,doe) {
    if (m <= 2) y--
    era = int((y >= 0 ? y : y-399)/400)
    yoe = y - era*400
    doy = int((153*(m + (m>2 ? -3 : 9)) + 2)/5) + d-1
    doe = yoe*365 + int(yoe/4) - int(yoe/100) + doy
    return era*146097 + doe - 719468
  }
  function cal(z,  era,doe,yoe,doy,mp,y,m,d) {
    z += 719468
    era = int((z >= 0 ? z : z - 146096)/146097)
    doe = z - era*146097
    yoe = int((doe - int(doe/1460) + int(doe/36524) - int(doe/146096))/365)
    y   = yoe + era*400
    doy = doe - (365*yoe + int(yoe/4) - int(yoe/100))
    mp  = int((5*doy + 2)/153)
    d   = doy - int((153*mp + 2)/5) + 1
    m   = mp + (mp < 10 ? 3 : -9)
    if (m <= 2) y++
    return sprintf("%04d-%02d-%02d", y, m, d)
  }
  # Returns BAD rather than a wrong number for anything that is not a date.
  # A feed that writes "N/A" instead of an empty cell would otherwise split()
  # into zeros and place the name a couple of millennia out.
  function tod(s,  p) {
    if (s !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/) return BAD
    split(s, p, "-")
    if (p[2]+0 < 1 || p[2]+0 > 12 || p[3]+0 < 1 || p[3]+0 > 31) return BAD
    return g(p[1]+0, p[2]+0, p[3]+0)
  }

  BEGIN {
    t0 = tod(today)
    if (t0 == BAD) { print "cohort: bad date " today > "/dev/stderr"; exit 2 }
  }

  # FILENAME, not NR==FNR: an EMPTY sectors.tsv makes FNR==NR true for the
  # first rows of the SECOND file, which would quietly eat the universe.
  FILENAME == stf { if ($1 != "") sec[$1] = $2; next }

  FNR == 1 && $1 == "symbol" { next }
  {
    sym = $1; le = $8
    if (sym == "") next
    s = sec[sym]
    if (s == "" || s == "other") next
    e = tod(le)
    if (e == BAD) next
    e += q
    d = e - t0
    if (d >= wmin && d <= wmax) printf "%s\t%s\t%s\t%d\n", sym, s, cal(e), d
  }
' "$st" "$uq" | LC_ALL=C sort -t"$tab" -k4,4n -k1,1
