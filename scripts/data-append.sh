#!/bin/bash
# Append one JSON record to the daily data corpus. Companion to tick-append.sh.
#
# Usage:
#   scripts/data-append.sh KIND DATE 'JSON_OBJECT'
#
# KIND: orders | quotes | decisions | events | counterfactuals
#   orders    — every preview/place/cancel/fill-verify: timestamps (request +
#               confirm, so approval latency is derivable), symbol, side, qty,
#               limit, fill price, order id, outcome
#   quotes    — every quote read at a §4.9/§4.10 gate: symbol, bid/ask/last,
#               quote timestamp, what decision it fed
#   decisions — anything decided with reasoning: entries taken/skipped and why,
#               thesis re-verification at the live price, cadence changes,
#               escalation choices. The reinforcement half of the corpus.
#   events    — everything else worth replaying: approvals, rejects, alerts,
#               trips, restarts, anomalies
#   counterfactuals — every time a rule BINDS: what was wanted, what was done
#               instead, which rule, and a reference price so the road not
#               taken can be marked to market later. Schema (all fields):
#               {"t":"HH:MM:SS","rule":"§3.1","wanted":"BUY 6 USB @65.10",
#                "did":"BUY 3 USB @65.10","symbol":"USB","ref_price":65.10,
#                "score_at":"2026-08-21","notes":"cap bound at 35%"}
#
# DATE: YYYY-MM-DD, Eastern (from get_datetime, never the machine clock).
# JSON_OBJECT: one single-quoted JSON object; validated with jq before append
#   so the corpus stays machine-parseable. Include a "t" field (HH:MM:SS ET).
#
# Appends to status/data/DATE-KIND.jsonl. Commit with the session close.
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "data-append: expected KIND DATE JSON, got $# args" >&2
  exit 2
fi

kind="$1"; date="$2"; json="$3"

case "$kind" in
  orders|quotes|decisions|events|counterfactuals) ;;
  *) echo "data-append: KIND must be orders|quotes|decisions|events|counterfactuals, got '$kind'" >&2; exit 2 ;;
esac

case "$date" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "data-append: DATE must be YYYY-MM-DD, got '$date'" >&2; exit 2 ;;
esac

if ! printf '%s' "$json" | jq -e 'type == "object"' >/dev/null 2>&1; then
  echo "data-append: payload is not a valid JSON object" >&2
  exit 2
fi

dir="$(cd "$(dirname "$0")/.." && pwd)/status/data"
file="$dir/${date}-${kind}.jsonl"
mkdir -p "$dir"

# Compact to one line so the file stays one-record-per-line.
printf '%s\n' "$(printf '%s' "$json" | jq -c .)" >> "$file"
echo "recorded: ${date}-${kind}"
