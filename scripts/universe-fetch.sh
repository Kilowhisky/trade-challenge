#!/bin/bash
# universe-fetch.sh — download the Nasdaq Trader symbol directory and reduce it
# to tradeable major-exchange symbols. Free, keyless, ~1 MB.
#
# Usage: scripts/universe-fetch.sh --out FILE
# Exit: 0 ok · 2 usage · 3 directory unreachable OR the filtered result came
#       back implausibly small (caller keeps last week's list either way —
#       see SANITY_FLOOR below).
set -uo pipefail
# UNIVERSE_FETCH_URL overrides the source for testing only (e.g. a local
# file:// fixture standing in for a redirect-to-decoy-page response). Never
# set it in normal use — the documented interface is `--out FILE` only.
URL="${UNIVERSE_FETCH_URL:-https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt}"
# The real directory currently yields ~11,227 tradeable symbols. A moved
# file, maintenance page, or edge/WAF block can come back as an HTTP
# "success" whose body is a short decoy page (e.g. a 3xx redirect to a small
# "File Not Found" HTML page — curl -f does not treat a 3xx status itself as
# failure) rather than the real directory. The python filter below then
# parses that HTML as pipe-delimited CSV and quietly produces zero rows,
# which without this floor would write an empty universe and exit 0 —
# indistinguishable from a real sweep that legitimately found nothing. 1000
# is far below the real count and far above zero/garbage. A false trip here
# is SAFE by design: exit 3 just means the caller keeps last week's universe
# instead of a worthless one.
SANITY_FLOOR=1000
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) shift; [ $# -gt 0 ] || { echo "universe-fetch: --out needs a path" >&2; exit 2; }; out="$1" ;;
    *) echo "universe-fetch: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$out" ] || { echo "universe-fetch: --out is required" >&2; exit 2; }

raw="$(mktemp)"; filtered="$(mktemp)"; trap 'rm -f "$raw" "$filtered"' EXIT
# -L: follow redirects rather than silently accepting a 3xx response body as
# a "successful" fetch. A true moved/missing file then has a chance to
# surface as a real HTTP failure below (curl -f on the final >=400 status)
# instead of a decoy page ever reaching the python filter. This is defense
# in depth, not a substitute for SANITY_FLOOR: a redirect target that itself
# returns 200 with a soft "not found" page would still slip past -f, which
# is exactly the case the floor exists to catch.
if ! curl -sf -L --max-time 60 "$URL" -o "$raw"; then
  echo "universe-fetch: directory unreachable at $URL — caller must keep the previous universe" >&2
  exit 3
fi
[ -s "$raw" ] || { echo "universe-fetch: directory came back empty" >&2; exit 3; }

n=$(python3 - "$raw" "$filtered" <<'PY'
import csv, sys
src, dst = sys.argv[1], sys.argv[2]
MAJOR = {'N', 'Q', 'A', 'P', 'Z'}
JUNK = (' warrant', '% note', ' right', ' unit', 'preferred',
        ' depositary', 'when issued', ' due 20')
keep = []
for r in csv.DictReader(open(src), delimiter='|'):
    sym = (r.get('Symbol') or '').strip()
    if not sym or '$' in sym or len(sym) > 5:            continue
    if r.get('Listing Exchange') not in MAJOR:           continue
    if r.get('Test Issue') != 'N':                       continue
    name = (r.get('Security Name') or '').lower()
    if any(k in name for k in JUNK):                     continue
    keep.append(sym)
keep = sorted(set(keep))
with open(dst, 'w') as f:
    if keep:
        f.write('\n'.join(keep) + '\n')
print(len(keep))
PY
)
py_rc=$?
if [ "$py_rc" -ne 0 ] || ! [[ "$n" =~ ^[0-9]+$ ]]; then
  echo "universe-fetch: filter step failed (rc=$py_rc)" >&2
  exit 3
fi

# Sanity floor — see the comment on SANITY_FLOOR above. Do NOT write $out at
# all when it isn't met: a partial/empty output file left on disk is its own
# trap, since a later run (or the caller) could read it back as valid.
if [ "$n" -lt "$SANITY_FLOOR" ]; then
  echo "universe-fetch: fetched $n symbols, refusing to write an empty/implausible universe (floor $SANITY_FLOOR) — caller must keep the previous universe" >&2
  exit 3
fi

mv "$filtered" "$out"
echo "universe-fetch: $n tradeable symbols" >&2
