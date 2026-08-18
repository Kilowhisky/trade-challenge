#!/bin/bash
# universe-fetch.sh — download the Nasdaq Trader symbol directory and reduce it
# to tradeable major-exchange symbols. Free, keyless, ~1 MB.
#
# Usage: scripts/universe-fetch.sh --out FILE
# Exit: 0 ok · 2 usage · 3 directory unreachable (caller keeps last week's list)
set -uo pipefail
URL="https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) shift; [ $# -gt 0 ] || { echo "universe-fetch: --out needs a path" >&2; exit 2; }; out="$1" ;;
    *) echo "universe-fetch: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$out" ] || { echo "universe-fetch: --out is required" >&2; exit 2; }

raw="$(mktemp)"; trap 'rm -f "$raw"' EXIT
if ! curl -sf --max-time 60 "$URL" -o "$raw"; then
  echo "universe-fetch: directory unreachable at $URL — caller must keep the previous universe" >&2
  exit 3
fi
[ -s "$raw" ] || { echo "universe-fetch: directory came back empty" >&2; exit 3; }

python3 - "$raw" "$out" <<'PY'
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
open(dst, 'w').write('\n'.join(keep) + '\n')
print("universe-fetch: %d tradeable symbols" % len(keep), file=sys.stderr)
PY
