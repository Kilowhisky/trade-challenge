#!/bin/bash
# Validated writer for the sector tag store.
#
# Usage: scripts/sector-write.sh SYMBOL SECTOR [DATE]
#        scripts/sector-write.sh --batch [DATE] <<'EOF'
#        SYMBOL SECTOR
#        ...
#        EOF
#
# DATE is YYYY-MM-DD Eastern and SHOULD be passed by callers, sourced from
# get_datetime rather than the machine clock — the same contract every other
# writer in this tree uses (see research-append.sh). It is optional only so an
# interactive re-tag is not tedious; omitting it falls back to the host's
# Eastern date, which is right whenever the host clock is.
#
# --batch exists for the weekly sector tagger (.claude/commands/sector-tag.md):
# a ~3,000-name qualified universe cannot be tagged one Bash call at a time,
# and the container's permission gate accepts exactly one multi-line form — a
# heredoc on a bare script path. Lines are `SYMBOL SECTOR` (whitespace or tab
# separated); blank lines and `#` comments are ignored. EVERY line is validated
# before ANY row is written, so a typo on line 200 refuses the whole batch
# rather than storing 199 rows and a corrupt one. Exit 2 names the bad line.
#
# Sectors are a CLOSED set. A typo must be refused rather than stored: an
# unrecognised value would silently shrink the universe the scout sweeps, and
# a universe that quietly gets smaller is the v1 failure mode all over again.
set -euo pipefail

valid_symbol() { case "$1" in *[!A-Za-z0-9.-]*|"") return 1 ;; esac; return 0; }
valid_sector() { case "$1" in consumer-software|airlines-transport|semis-hardware|other) return 0 ;; esac; return 1; }
valid_date()   { case "$1" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) return 0 ;; esac; return 1; }

batch=0
if [ "${1:-}" = "--batch" ]; then
  batch=1; shift
  [ "$#" -le 1 ] || { echo "sector-write: --batch takes at most a DATE, got $#" >&2; exit 2; }
  date_arg="${1:-}"
else
  if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "sector-write: expected SYMBOL SECTOR [DATE] or --batch [DATE], got $#" >&2; exit 2
  fi
  symbol="$1"; sector="$2"; date_arg="${3:-}"
  valid_symbol "$symbol" || { echo "sector-write: bad symbol '$symbol'" >&2; exit 2; }
  valid_sector "$sector" || { echo "sector-write: unknown sector '$sector'" >&2; exit 2; }
fi
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
  valid_date "$date_arg" || { echo "sector-write: DATE must be YYYY-MM-DD, got '$date_arg'" >&2; exit 2; }
  today="$date_arg"
else
  today="$(TZ=America/New_York date +%Y-%m-%d)"
fi

# Batch: read and validate every line BEFORE touching the file. The rows are
# collected into a scratch file (symbol \t sector) so the write below is one
# awk pass in both modes.
rows="$(mktemp)"
trap 'rm -f "$rows"; release_lock' EXIT INT TERM
if [ "$batch" -eq 1 ]; then
  n=0; lineno=0
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno+1))
    case "$line" in ''|'#'*) continue ;; esac
    set -- $line
    if [ "$#" -ne 2 ]; then
      echo "sector-write: line $lineno: expected 'SYMBOL SECTOR', got '$line'" >&2; exit 2
    fi
    valid_symbol "$1" || { echo "sector-write: line $lineno: bad symbol '$1'" >&2; exit 2; }
    valid_sector "$2" || { echo "sector-write: line $lineno: unknown sector '$2'" >&2; exit 2; }
    printf '%s\t%s\n' "$1" "$2" >> "$rows"
    n=$((n+1))
  done
  [ "$n" -gt 0 ] || { echo "sector-write: --batch read no rows" >&2; exit 2; }
else
  printf '%s\t%s\n' "$symbol" "$sector" > "$rows"
fi

tmp="$file.tmp.$$"
# Re-tagging UPDATES in place: every existing row for a symbol in this write is
# dropped, then the new rows are appended. $1"" forces STRING comparison —
# without the concatenation awk treats a numeric-looking field as a strnum and
# compares numerically, so tagging "1E2" deletes the row for "100" (and on BSD
# awk, "NAN" too — a real listed ticker). Silent row loss, exit 0: exactly the
# "universe that quietly gets smaller" this file's header warns about. A batch
# that names the same symbol twice keeps the LAST line, like two single calls.
if [ -f "$file" ]; then
  awk -F'\t' 'FILENAME == ARGV[1] { drop[$1""] = 1; next } !($1"" in drop)' "$rows" "$file" > "$tmp"
else
  : > "$tmp"
fi
awk -F'\t' -v d="$today" '{ last[$1""] = $2; if (!($1"" in seen)) { seen[$1""] = 1; order[++n] = $1"" } }
  END { for (i = 1; i <= n; i++) printf "%s\t%s\t%s\n", order[i], last[order[i]], d }' "$rows" >> "$tmp"
mv "$tmp" "$file"
written="$(wc -l < "$rows" | tr -d ' ')"
echo "sector-write: tagged $written symbol(s) in $file" >&2
