#!/bin/bash
# Append one compact per-underlying OI snapshot record to the daily OI file.
# Companion to data-append.sh; the research-scout's ONLY other sanctioned
# write path besides research-write.sh (spec: 2026-08-14-research-loop-design.md §8.2).
#
# Usage:
#   scripts/oi-append.sh DATE 'JSON_OBJECT'
#
# DATE: YYYY-MM-DD, Eastern (from get_datetime, never the machine clock).
# JSON_OBJECT: one single-quoted JSON object, validated with jq. Required
#   fields: "symbol" (underlying) and "t" (HH:MM:SS ET). Suggested shape:
#   {"symbol":"XYZ","t":"16:20:00","spot":123.45,
#    "call_oi":12345,"put_oi":6789,
#    "contracts":[{"osi":"XYZ   260918C00120000","oi":1500,"vol":200,
#                  "iv":0.31,"bid":4.10,"ask":4.30}, ...]}
#   Bound the contracts array per research.md §B-oi (±20% of spot,
#   ≤ 60 DTE, OI ≥ 100, cap ~40) — snapshots are for diffing, not archiving
#   whole chains.
#
# Appends to research/oi/DATE.jsonl. One line per underlying per day; the
# post-close pass diffs today's file against the most recent prior file.
# Commit with the session close.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "oi-append: expected DATE JSON, got $# args" >&2
  exit 2
fi

date="$1"; json="$2"

if ! [[ "$date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "oi-append: DATE must be YYYY-MM-DD, got '$date'" >&2
  exit 2
fi

if ! printf '%s' "$json" | jq -e 'type == "object" and has("symbol") and has("t")' > /dev/null 2>&1; then
  echo "oi-append: JSON failed validation (must be an object with \"symbol\" and \"t\")" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
file="$repo_root/research/oi/$date.jsonl"
sym="$(printf '%s' "$json" | jq -r .symbol)"

if [ -f "$file" ] && jq -e -s --arg s "$sym" 'map(select(.symbol == $s)) | length > 0' "$file" >/dev/null 2>&1; then
  echo "oi-append: refused — $sym already snapshotted in oi/$date.jsonl today (idempotency guard, design rev2 §8.1). Not an error; skip this underlying." >&2
  exit 4
fi

mkdir -p "$repo_root/research/oi"
printf '%s' "$json" | jq -c . >> "$file"
echo "oi-append: appended $(printf '%s' "$json" | jq -r .symbol) to research/oi/$date.jsonl"
