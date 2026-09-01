#!/bin/bash
# Regression suite for cohort.sh — the earnings cohort builder.
#
# Two things this suite is deliberately built to catch:
#
#   1. THE WINDOW MUST COME FROM rules.yml. Every other rule parameter in this
#      repo lives in rules.yml exactly once, and check-consistency.sh fails the
#      build when a document or script states a second copy. Its hard-code grep
#      only matches percentage arithmetic (`* 35 / 100`), so a bare
#      `WIN_MIN=21` would sail straight past it. The rules-sourced assertion
#      below is the only thing standing between this script and a silently
#      diverging second copy of the entry window.
#
#   2. THE DATE ARITHMETIC MUST BE RIGHT ON BSD AND GNU ALIKE. cohort.sh may
#      not shell out to `date -d` (GNU-only), so it carries its own
#      days-since-epoch conversion. This suite generates every fixture date
#      with python3's calendar — a completely independent implementation — so
#      a wrong day count in the awk shows up as a failed boundary assertion
#      rather than as a cohort that is quietly off by a week.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
export TC_RESEARCH_DIR="$(mktemp -d)"
WORK="$(mktemp -d)"
trap 'rm -rf "$TC_RESEARCH_DIR" "$WORK"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

command -v python3 >/dev/null 2>&1 || {
  echo "test-cohort: python3 is required to generate fixture dates" >&2; exit 2; }

# The window is read straight out of rules.yml rather than restated here: a
# test that hard-codes 21/42 stops testing the moment the rule moves.
WMIN="$(awk -F': *' '/^  scout_entry_window_min_days:/{print $2; exit}' rules.yml | tr -d ' \r')"
WMAX="$(awk -F': *' '/^  scout_entry_window_max_days:/{print $2; exit}' rules.yml | tr -d ' \r')"
[ -n "$WMIN" ] && [ -n "$WMAX" ] || { echo "test-cohort: no scout window in rules.yml" >&2; exit 2; }
WMID=$(( (WMIN + WMAX) / 2 ))
QUARTER=91

# TODAY is fixed so the suite does not drift with the wall clock.
TODAY=2026-09-01

# last_earnings for a name that should land N days out:  TODAY + N - QUARTER.
# python3's date class is the independent calendar this suite checks the awk
# against; it is a test-only dependency and cohort.sh must never acquire it.
le() { python3 -c 'import sys,datetime
t=datetime.date.fromisoformat(sys.argv[1])
print(t+datetime.timedelta(days=int(sys.argv[2])-int(sys.argv[3])))' "$TODAY" "$1" "$QUARTER"; }
# The estimated print date a given last_earnings implies.
est() { python3 -c 'import sys,datetime
print(datetime.date.fromisoformat(sys.argv[1])+datetime.timedelta(days=int(sys.argv[2])))' "$1" "$QUARTER"; }

LE_LO="$(le $((WMIN-1)))"   # one day short of the window  -> excluded
LE_MIN="$(le "$WMIN")"      # exactly the near edge        -> included
LE_MID="$(le "$WMID")"      # comfortably inside           -> included
LE_MAX="$(le "$WMAX")"      # exactly the far edge         -> included
LE_HI="$(le $((WMAX+1)))"   # one day past the window      -> excluded

write_fixture() {
  # Rows are written in DESCENDING days_out order on purpose: unsorted output
  # would come back backwards, so the ordering assertion cannot pass by luck.
  { printf 'symbol\tprice\tadv10\tdollar_vol\tpct\toptionable\tlev\tlast_earnings\tis_etf\trange\n'
    printf 'EDGEHI\t10\t1\t1\t0\ttrue\t0\t%s\tfalse\t1\n'  "$LE_HI"
    printf 'EDGEMAX\t10\t1\t1\t0\ttrue\t0\t%s\tfalse\t1\n' "$LE_MAX"
    printf 'EDGEMID\t10\t1\t1\t0\ttrue\t0\t%s\tfalse\t1\n' "$LE_MID"
    printf 'EDGEMIN\t10\t1\t1\t0\ttrue\t0\t%s\tfalse\t1\n' "$LE_MIN"
    printf 'EDGELO\t10\t1\t1\t0\ttrue\t0\t%s\tfalse\t1\n'  "$LE_LO"
    printf 'NODATE\t10\t1\t1\t0\ttrue\t0\t\tfalse\t1\n'
    printf 'BADDATE\t10\t1\t1\t0\ttrue\t0\tN/A\tfalse\t1\n'
    printf 'OTHERS\t10\t1\t1\t0\ttrue\t0\t%s\tfalse\t1\n'  "$LE_MID"
    printf 'UNTAGD\t10\t1\t1\t0\ttrue\t0\t%s\tfalse\t1\n'  "$LE_MID"
  } > "$TC_RESEARCH_DIR/universe-qualified.tsv"
  { printf 'EDGEHI\tairlines-transport\t2026-08-31\n'
    printf 'EDGEMAX\tsemis-hardware\t2026-08-31\n'
    printf 'EDGEMID\tconsumer-software\t2026-08-31\n'
    printf 'EDGEMIN\tconsumer-software\t2026-08-31\n'
    printf 'EDGELO\tconsumer-software\t2026-08-31\n'
    printf 'NODATE\tconsumer-software\t2026-08-31\n'
    printf 'BADDATE\tconsumer-software\t2026-08-31\n'
    printf 'OTHERS\tother\t2026-08-31\n'
  } > "$TC_RESEARCH_DIR/sectors.tsv"
}
write_fixture

out="$(./scripts/cohort.sh "$TODAY" 2>/dev/null)"
syms="$(printf '%s\n' "$out" | awk -F'\t' 'NF{print $1}' | sort | tr '\n' ' ')"
order="$(printf '%s\n' "$out" | awk -F'\t' 'NF{printf "%s ", $1}')"
field() { printf '%s\n' "$out" | awk -F'\t' -v s="$1" -v c="$2" '$1==s{print $c; exit}'; }

echo "== window membership (window is ${WMIN}-${WMAX} days, from rules.yml) =="

[ "$syms" = "EDGEMAX EDGEMID EDGEMIN " ] \
  && ok "cohort is exactly the names inside the ${WMIN}-${WMAX} day window" \
  || bad "cohort was '$syms', want 'EDGEMAX EDGEMID EDGEMIN '"

# The two boundary assertions are called out separately from the membership
# assertion above so that an off-by-one in the comparison names itself instead
# of hiding inside a set difference.
printf '%s\n' "$out" | grep -q '^EDGEMIN	' \
  && ok "a name at exactly ${WMIN} days out is INSIDE the window (>= not >)" \
  || bad "the name at exactly ${WMIN} days out was excluded — near edge is exclusive"
printf '%s\n' "$out" | grep -q '^EDGELO	' \
  && bad "a name at $((WMIN-1)) days out entered the cohort" \
  || ok "a name at $((WMIN-1)) days out is excluded"
printf '%s\n' "$out" | grep -q '^EDGEMAX	' \
  && ok "a name at exactly ${WMAX} days out is INSIDE the window (<= not <)" \
  || bad "the name at exactly ${WMAX} days out was excluded — far edge is exclusive"
printf '%s\n' "$out" | grep -q '^EDGEHI	' \
  && bad "a name at $((WMAX+1)) days out entered the cohort" \
  || ok "a name at $((WMAX+1)) days out is excluded"

echo "== exclusions =="

printf '%s\n' "$out" | grep -q NODATE \
  && bad "a name with no last_earnings entered the cohort" \
  || ok "a missing last_earnings is skipped, not crashed on"
# A feed that writes 'N/A' rather than an empty cell must not be parsed as a
# date: split() on it yields zeros, which places the name ~2000 years out and
# is only harmless by accident.
printf '%s\n' "$out" | grep -q BADDATE \
  && bad "an unparseable last_earnings entered the cohort" \
  || ok "an unparseable last_earnings is skipped"
printf '%s\n' "$out" | grep -q OTHERS \
  && bad "a name tagged 'other' entered the cohort" \
  || ok "sector 'other' is excluded"
printf '%s\n' "$out" | grep -q UNTAGD \
  && bad "an untagged name entered the cohort" \
  || ok "untagged names are excluded"

echo "== output shape =="

# Sorted by days_out ASCENDING. The fixture is written in the reverse order, so
# passing this requires an actual sort.
[ "$order" = "EDGEMIN EDGEMID EDGEMAX " ] \
  && ok "rows are sorted by days_out ascending" \
  || bad "row order was '$order', want 'EDGEMIN EDGEMID EDGEMAX '"

n="$(printf '%s\n' "$out" | awk -F'\t' 'NF{print NF}' | sort -u | tr '\n' ' ')"
[ "$n" = "4 " ] && ok "every row has 4 tab-separated fields" \
  || bad "row field counts were '$n', want '4 '"

[ "$(field EDGEMAX 2)" = "semis-hardware" ] \
  && ok "column 2 carries the sector tag from sectors.tsv" \
  || bad "EDGEMAX sector was '$(field EDGEMAX 2)', want 'semis-hardware'"

# Column 3 is the ESTIMATED NEXT print, not the last one. A downstream reader
# that gets last_earnings here sees a catalyst 91 days in the PAST and treats
# a live setup as already spent.
want_est="$(est "$LE_MIN")"
[ "$(field EDGEMIN 3)" = "$want_est" ] \
  && ok "column 3 is the ESTIMATED next print (last_earnings + ${QUARTER}d)" \
  || bad "EDGEMIN est_next_earnings was '$(field EDGEMIN 3)', want '$want_est'"

echo "== date arithmetic, cross-checked against an independent calendar =="

# python3 computed the fixture dates; the awk computed these day counts. They
# agree only if the days-since-epoch conversion is right — which is what makes
# the boundary assertions above mean anything.
[ "$(field EDGEMIN 4)" = "$WMIN" ] \
  && ok "days_out for the near-edge name is exactly $WMIN" \
  || bad "EDGEMIN days_out was '$(field EDGEMIN 4)', want $WMIN"
[ "$(field EDGEMAX 4)" = "$WMAX" ] \
  && ok "days_out for the far-edge name is exactly $WMAX" \
  || bad "EDGEMAX days_out was '$(field EDGEMAX 4)', want $WMAX"

# A leap day inside the span is where a hand-rolled calendar breaks. 2028-02-29
# sits between this last_earnings and its estimated print.
{ printf 'symbol\tprice\tadv10\tdollar_vol\tpct\toptionable\tlev\tlast_earnings\tis_etf\trange\n'
  printf 'LEAPER\t10\t1\t1\t0\ttrue\t0\t2027-12-15\tfalse\t1\n'
} > "$TC_RESEARCH_DIR/universe-qualified.tsv"
printf 'LEAPER\tsemis-hardware\t2026-08-31\n' > "$TC_RESEARCH_DIR/sectors.tsv"
leap_today="$(python3 -c 'import datetime
print(datetime.date(2027,12,15)+datetime.timedelta(days=91-'"$WMIN"'))')"
leap="$(./scripts/cohort.sh "$leap_today" 2>/dev/null)"
[ "$(printf '%s\n' "$leap" | awk -F'\t' '$1=="LEAPER"{print $4}')" = "$WMIN" ] \
  && ok "a span crossing the 2028-02-29 leap day counts correctly" \
  || bad "leap-day span gave days_out '$(printf '%s\n' "$leap" | awk -F'\t' '$1=="LEAPER"{print $4}')', want $WMIN"
write_fixture

echo "== the window is read from rules.yml, not hard-coded =="

# The load-bearing assertion. Run a COPY of the script against a COPY of
# rules.yml with the window narrowed by a day at each end: both edge names must
# fall out. A script carrying its own literal 21/42 keeps emitting all three
# and fails here — which is the only place it would fail at all.
mkdir -p "$WORK/scripts"
cp scripts/cohort.sh scripts/lib-rules.sh "$WORK/scripts/" 2>/dev/null
cp rules.yml "$WORK/" 2>/dev/null
if [ -f "$WORK/scripts/cohort.sh" ]; then
  sed -i.bak \
    -e "s/^  scout_entry_window_min_days:.*/  scout_entry_window_min_days: $((WMIN+1))/" \
    -e "s/^  scout_entry_window_max_days:.*/  scout_entry_window_max_days: $((WMAX-1))/" \
    "$WORK/rules.yml"
  narrowed="$(TC_RESEARCH_DIR="$TC_RESEARCH_DIR" "$WORK/scripts/cohort.sh" "$TODAY" 2>/dev/null \
              | awk -F'\t' 'NF{print $1}' | sort | tr '\n' ' ')"
else
  narrowed="<script absent>"
fi
[ "$narrowed" = "EDGEMID " ] \
  && ok "narrowing the window in rules.yml drops both edge names" \
  || bad "narrowed cohort was '$narrowed', want 'EDGEMID ' — the window is not read from rules.yml"

echo "== an empty cohort is a correct answer, not a failure =="

# Between earnings seasons the cohort is legitimately empty. If that exits
# non-zero the scheduled job reports a failure every day from August to
# October and the deadman watchdog cries wolf until nobody reads it.
: > "$TC_RESEARCH_DIR/universe-qualified.tsv"
eout="$(./scripts/cohort.sh "$TODAY" 2>/dev/null)"; rc=$?
[ "$rc" -eq 0 ] && ok "an empty cohort exits 0" || bad "an empty cohort exits $rc, want 0"
[ -z "$eout" ] && ok "an empty cohort prints nothing" || bad "an empty cohort printed '$eout'"

# Same reasoning for the inputs not existing yet: before the first weekly sweep
# has run there is no universe file, and that is not a failure either.
rm -f "$TC_RESEARCH_DIR/universe-qualified.tsv" "$TC_RESEARCH_DIR/sectors.tsv"
mout="$(./scripts/cohort.sh "$TODAY" 2>/dev/null)"; rc=$?
[ "$rc" -eq 0 ] && [ -z "$mout" ] \
  && ok "missing input files exit 0 with no output" \
  || bad "missing inputs exited $rc with output '$mout'"
write_fixture

echo "== bad arguments are refused =="

# The opposite of the rule above: a MALFORMED today is a real failure. Silently
# accepting one shifts the entire cohort and nothing downstream can tell.
./scripts/cohort.sh >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a missing date argument" || bad "accepted no arguments"
./scripts/cohort.sh 2026-9-1 >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses an unpadded date" || bad "accepted '2026-9-1'"
./scripts/cohort.sh 2026-13-01 >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses an out-of-range month" || bad "accepted month 13"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
