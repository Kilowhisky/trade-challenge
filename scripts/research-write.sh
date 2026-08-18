#!/bin/bash
# Replace research/candidates.md from stdin. The ONLY sanctioned write path
# for candidates.md (spec: 2026-08-14-research-loop-design.md §3;
# concurrency: 2026-08-14-deep-research-design.md §8.2).
#
# Usage:
#   scripts/research-write.sh [--expect-last-pass 'Last pass: <value>'] <<'EOF'
#   ...full new contents of candidates.md...
#   EOF
#
# --expect-last-pass: optimistic concurrency. Pass the FULL "Last pass:" line
#   you read at compose time. If the file's current line differs, the write is
#   REFUSED (exit 3): another writer landed while you composed. Re-read the
#   file, merge your changes onto the fresh copy, and try again. Never retry
#   with your stale copy. All routine callers (scout, deep-research) MUST use
#   this flag; omitting it is for interactive/manual repair only.
#
# Validates before writing (unchanged from v1): >10 lines, canonical H1,
# order-parameters banner, "Last pass:" line present.
# Lock: mkdir lock via lib-lock.sh (exit 5 on timeout). Previous version kept
# at research/candidates.md.prev.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
target="$repo_root/research/candidates.md"
# shellcheck source=lib-lock.sh
. "$repo_root/scripts/lib-lock.sh"

expect=""
if [ "$#" -eq 2 ] && [ "$1" = "--expect-last-pass" ]; then
  expect="$2"
elif [ "$#" -ne 0 ]; then
  echo "research-write: usage: research-write.sh [--expect-last-pass 'Last pass: <value>'] < content" >&2
  exit 2
fi

content="$(cat)"

line_count="$(printf '%s\n' "$content" | wc -l | tr -d ' ')"
if [ "$line_count" -le 10 ]; then
  echo "research-write: refused — only $line_count lines (truncated heredoc?)" >&2
  exit 1
fi

first_line="$(printf '%s\n' "$content" | head -1)"
if [ "$first_line" != "# Research candidates" ]; then
  echo "research-write: refused — first line must be '# Research candidates'" >&2
  exit 1
fi

if ! printf '%s\n' "$content" | grep -q "never a source for order parameters"; then
  echo "research-write: refused — missing the order-parameters banner" >&2
  exit 1
fi

if ! printf '%s\n' "$content" | grep -q "^Last pass:"; then
  echo "research-write: refused — missing the 'Last pass:' timestamp line" >&2
  exit 1
fi

mkdir -p "$repo_root/research"
if ! acquire_lock "$target" 10; then
  echo "research-write: refused — could not acquire lock (another writer active?)" >&2
  exit 5
fi

if [ -n "$expect" ] && [ -f "$target" ]; then
  current="$(grep '^Last pass:' "$target" | head -1 || true)"
  if [ "$current" != "$expect" ]; then
    echo "research-write: refused — Last pass mismatch (CAS)." >&2
    echo "  expected: $expect" >&2
    echo "  current:  $current" >&2
    echo "  Another writer landed. Re-read candidates.md, merge, retry." >&2
    exit 3
  fi
fi

if [ -f "$target" ]; then
  cp "$target" "$target.prev"
fi
printf '%s\n' "$content" > "$target"
echo "research-write: wrote $line_count lines to research/candidates.md"
