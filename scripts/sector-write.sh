#!/bin/bash
# Validated writer for the sector tag store.
#
# Usage: scripts/sector-write.sh SYMBOL SECTOR [DATE]
#
# DATE is YYYY-MM-DD Eastern and SHOULD be passed by callers, sourced from
# get_datetime rather than the machine clock — the same contract every other
# writer in this tree uses (see research-append.sh). It is optional only so an
# interactive re-tag is not tedious; omitting it falls back to the host's
# Eastern date, which is right whenever the host clock is.
#
# Sectors are a CLOSED set. A typo must be refused rather than stored: an
# unrecognised value would silently shrink the universe the scout sweeps, and
# a universe that quietly gets smaller is the v1 failure mode all over again.
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "sector-write: expected SYMBOL SECTOR [DATE], got $#" >&2; exit 2
fi
symbol="$1"; sector="$2"; date_arg="${3:-}"

case "$symbol" in
  *[!A-Za-z0-9.-]*|"") echo "sector-write: bad symbol '$symbol'" >&2; exit 2 ;;
esac
case "$sector" in
  consumer-software|airlines-transport|semis-hardware|other) ;;
  *) echo "sector-write: unknown sector '$sector'" >&2; exit 2 ;;
esac

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib-lock.sh
. "$repo_root/scripts/lib-lock.sh"

dir="${TC_RESEARCH_DIR:-$repo_root/research}"
mkdir -p "$dir"
file="$dir/sectors.tsv"

# Read-modify-write. Unlocked, 20 concurrent writes of distinct symbols lost 16
# of them, every one exiting 0. research-write.sh, research-replace.sh and
# status-write.sh all take this lock; so does this.
if ! acquire_lock "$file" 10; then
  echo "sector-write: refused — could not acquire lock (another writer active?)" >&2
  exit 5
fi
if [ -n "$date_arg" ]; then
  case "$date_arg" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) echo "sector-write: DATE must be YYYY-MM-DD, got '$date_arg'" >&2; exit 2 ;;
  esac
  today="$date_arg"
else
  today="$(TZ=America/New_York date +%Y-%m-%d)"
fi

tmp="$file.tmp.$$"
if [ -f "$file" ]; then
  # $1"" and s"" force STRING comparison. Without the concatenation awk treats
  # a numeric-looking field as a strnum and compares numerically, so tagging
  # "1E2" deletes the row for "100" (and on BSD awk, "NAN" too — a real listed
  # ticker). Silent row loss, exit 0: exactly the "universe that quietly gets
  # smaller" this file's header warns about.
  awk -F'\t' -v s="$symbol" '$1"" != s""' "$file" > "$tmp"
else
  : > "$tmp"
fi
printf '%s\t%s\t%s\n' "$symbol" "$sector" "$today" >> "$tmp"
mv "$tmp" "$file"
