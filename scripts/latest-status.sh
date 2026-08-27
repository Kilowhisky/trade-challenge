#!/bin/bash
# Print the path of the most recent §7.2 status file, and optionally its
# recorded high-water mark.
#
#   scripts/latest-status.sh              -> status/YYYY-MM-DD.md
#   scripts/latest-status.sh --before DATE-> the newest file strictly before DATE
#   scripts/latest-status.sh --hwm        -> the HWM figure from that file
#
# Why an agent cannot just Glob for this. status/ is gitignored under §7.1
# (local-only, purged before the repo went public) and the Glob tool does not
# return matches under an ignored path. Measured in the container 2026-08-26:
# 11 status files present, `Glob status/*.md` reported 0. Read on an explicit
# path still works, which is exactly why this hid — an agent that guesses a
# filename succeeds and an agent that searches concludes the file does not
# exist. The first session-close run reported "no status file existed before
# this one" with 2026-08-25.md sitting right there, and reached the correct
# high-water mark only by falling back to CHANGELOG.md.
#
# Setting respectGitignore:false does NOT fix it — that governs the file
# picker, not Glob. Verified live in the container before this script existed.
#
# Read-only. Prints a path or a number; touches nothing.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
before=""
want_hwm=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --before) before="${2:-}"; shift 2
              [[ "$before" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
                || { echo "latest-status: bad --before DATE '$before'" >&2; exit 2; } ;;
    --hwm)    want_hwm=1; shift ;;
    *)        echo "latest-status: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

# ls, not find: the filenames are ISO dates, so lexical order IS chronological
# order, and this stays correct if a file's mtime is rewritten by a git
# checkout or a sidecar sync.
latest=""
for f in "$repo_root"/status/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md; do
  [ -e "$f" ] || continue
  d="$(basename "$f" .md)"
  [ -n "$before" ] && [ ! "$d" \< "$before" ] && continue
  latest="$f"
done

if [ -z "$latest" ]; then
  echo "latest-status: no status file found${before:+ before $before}" >&2
  exit 1
fi

if [ "$want_hwm" -eq 0 ]; then
  echo "${latest#"$repo_root"/}"
  exit 0
fi

# tick.md §B5: the mark comes from the "State recorded — current" block and
# ONLY that block. A status file can carry superseded blocks with
# near-identical headings, so the first "High-water mark" hit in the file may
# be stale. Read from that heading to the next one, and take the first match
# inside it.
hwm="$(awk '
  /^### State recorded — current$/ { inblock=1; next }
  inblock && /^#/                  { exit }
  inblock && /High-water mark:/    { print; exit }
' "$latest" | grep -oE '\$[0-9][0-9,]*\.[0-9]{2}' | head -1 || true)"

if [ -z "$hwm" ]; then
  echo "latest-status: no High-water mark line inside the 'State recorded — current' block of ${latest#"$repo_root"/}" >&2
  exit 1
fi
echo "$hwm"
