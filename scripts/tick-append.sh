#!/bin/bash
# Append one row to the session tick ledger, then print the canonical one-line
# tick summary. Called only by /tick; the printed line IS the line shown to
# Chris — derived from the same fields as the ledger row, by construction.
#
# Usage:
#   scripts/tick-append.sh DATE TIME_ET STATE COMP HWM DD LEVEL POS STOPS ORDERS \
#                          SETTLED UNSETTLED RESERVE FLAGS ['NOTE text']
#
# NOTE is optional free text. Single-quote it at the call site: this script
# joins multiple trailing words as a fallback and sanitizes tabs/newlines in
# every field, but it cannot undo what the shell does first to bare $, &, *,
# ; or quotes.
# RESERVE is the watch-2 total-cash figure in dollars (e.g. 900.00), never "ok".
# DATE and TIME_ET come from get_datetime (Eastern), never from this machine —
# the machine runs Pacific and would file a 21:15 ET tick under the wrong day.
set -euo pipefail

if [ "$#" -lt 14 ]; then
  echo "tick-append: expected at least 14 fields (DATE..FLAGS), got $#" >&2
  exit 2
fi

case "$1" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "tick-append: DATE must be YYYY-MM-DD, got '$1'" >&2; exit 2 ;;
esac

# Tab/newline-sanitize every argument so no field can shift TSV columns.
clean=()
for a in "$@"; do
  a="${a//[$'\t\r\n']/ }"
  clean+=("$a")
done
set -- "${clean[@]}"

date="$1"; time_et="$2"; state="$3"; comp="$4"; hwm="$5"; dd="$6"; level="$7"
pos="$8"; stops="$9"; orders="${10}"; settled="${11}"; unsettled="${12}"
reserve="${13}"; flags="${14}"
shift 14
if [ "$#" -gt 1 ]; then
  echo "tick-append: NOTE arrived as $# words — quote it; check the row for shell-mangling" >&2
fi
note="$*"
[ -n "$note" ] || note="-"

dir="$(cd "$(dirname "$0")/.." && pwd)/status/ticks"
file="$dir/${date}.tsv"

mkdir -p "$dir"
if [ ! -f "$file" ]; then
  printf 'time_et\tstate\tcomp_capital\thwm\tdd_pct\tlevel\tpositions\tstops\torders\tsettled\tunsettled\treserve\tflags\tnote\n' > "$file"
fi

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$time_et" "$state" "$comp" "$hwm" "$dd" "$level" "$pos" "$stops" "$orders" \
  "$settled" "$unsettled" "$reserve" "$flags" "$note" >> "$file"

line="${time_et} ET | ${state} | comp \$${comp} (${dd}%) | HWM \$${hwm} | ${level} | ${pos} pos / ${stops} stops | ${orders} orders | settled \$${settled} | reserve \$${reserve}"
[ "$flags" = "-" ] || line="${line} | TRIP:${flags}"
[ "$note" = "-" ] || line="${line} | ${note}"
printf '%s\n' "$line"
