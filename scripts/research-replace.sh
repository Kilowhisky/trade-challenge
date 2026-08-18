#!/bin/bash
# Locked, validated full-replacement writer for the deep-research file tree
# (design rev2 §8.2/§8.3). candidates.md has its own script (research-write.sh);
# this one covers the other replace-style targets:
#   roster          -> research/options-roster.md
#   preopen DATE    -> research/preopen/DATE.md
#   scorecard       -> research/scorecard.md
#   universe        -> research/universe.md
#   standing        -> research/standing.md
# Usage: scripts/research-replace.sh TARGET [DATE] <<'EOF' ... EOF
# Per-target validation enforces each file's required first line and the
# banner lines the spec demands verbatim (rev2 §5, §6.1, §7.1).
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib-lock.sh
. "$repo_root/scripts/lib-lock.sh"

if [ "$#" -lt 1 ]; then
  echo "research-replace: usage: research-replace.sh TARGET [DATE] < content" >&2; exit 2
fi
target_kind="$1"
require_verified_as_of=0

case "$target_kind" in
  roster)
    [ "$#" -eq 1 ] || { echo "research-replace: roster takes no DATE" >&2; exit 2; }
    file="$repo_root/research/options-roster.md"
    h1='# Options-viable roster'
    banners=("never a source for order parameters" "TTL") ;;
  preopen)
    [ "$#" -eq 2 ] || { echo "research-replace: preopen requires DATE" >&2; exit 2; }
    [[ "$2" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "research-replace: bad DATE '$2'" >&2; exit 2; }
    file="$repo_root/research/preopen/$2.md"
    h1="# Pre-open brief — $2"
    banners=("Pre-market data informs, it never qualifies") ;;
  scorecard)
    [ "$#" -eq 1 ] || { echo "research-replace: scorecard takes no DATE" >&2; exit 2; }
    file="$repo_root/research/scorecard.md"
    h1='# Research scorecard'
    banners=("never loosens a gate in-flight" "explicit conversation with Chris") ;;
  universe)
    [ "$#" -eq 1 ] || { echo "research-replace: universe takes no DATE" >&2; exit 2; }
    file="$repo_root/research/universe.md"
    h1='# Fallback universe'
    banners=("never a source for order parameters") ;;
  standing)
    [ "$#" -eq 1 ] || { echo "research-replace: standing takes no DATE" >&2; exit 2; }
    file="$repo_root/research/standing.md"
    h1='# Standing research reference'
    banners=("never a source for order parameters")
    require_verified_as_of=1 ;;
  *) echo "research-replace: TARGET must be roster|preopen|scorecard|universe|standing, got '$target_kind'" >&2; exit 2 ;;
esac

content="$(cat)"
line_count="$(printf '%s\n' "$content" | wc -l | tr -d ' ')"
if [ "$line_count" -le 5 ]; then
  echo "research-replace: refused — only $line_count lines (truncated heredoc?)" >&2; exit 1
fi
first_line="$(printf '%s\n' "$content" | head -1)"
if [ "$first_line" != "$h1" ]; then
  echo "research-replace: refused — first line must be '$h1'" >&2; exit 1
fi
for b in "${banners[@]}"; do
  if ! printf '%s\n' "$content" | grep -qF "$b"; then
    echo "research-replace: refused — required banner text missing: '$b'" >&2; exit 1
  fi
done
if [ "$require_verified_as_of" -eq 1 ]; then
  if ! printf '%s\n' "$content" | grep -q '^Verified as of:'; then
    echo "research-replace: refused — missing the 'Verified as of:' stamp line" >&2; exit 1
  fi
fi

mkdir -p "$(dirname "$file")"
if ! acquire_lock "$file" 10; then
  echo "research-replace: refused — could not acquire lock on $file" >&2; exit 5
fi
if [ -f "$file" ]; then cp "$file" "$file.prev"; fi
printf '%s\n' "$content" > "$file"
echo "research-replace: wrote $line_count lines to ${file#"$repo_root"/}"
