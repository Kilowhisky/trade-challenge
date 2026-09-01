#!/bin/bash
# Validated append for the per-name evidence ledger (design rev2 §8.3
# whitelist). Target: research/evidence/SYMBOL.jsonl
# Usage: scripts/evidence-append.sh SYMBOL DATE 'JSON_OBJECT'
# DATE: YYYY-MM-DD Eastern (from get_datetime, never the machine clock).
#
# Required JSON fields, all enforced:
#   claim         the specific falsifiable assertion
#   url           where it was observed
#   source_type   one of end-user | employee | counterparty | enthusiast |
#                 primary-doc | mainstream. `mainstream` is LEGAL on purpose:
#                 recording that a story has reached mainstream coverage is how
#                 the kill switch gets its evidence. It counts against
#                 escalation rather than toward it, which is a reader concern.
#   observed      YYYY-MM-DD, must equal the DATE argument
#   independence  why this source is not a restatement of another
#
# The ledger is the mechanism, not a log: the signal is the DELTA against a
# name's own history, so a corrupt observation date poisons every future
# comparison computed against it. Refuse rather than coerce.
set -euo pipefail

[ "$#" -eq 3 ] || { echo "evidence-append: expected SYMBOL DATE JSON, got $#" >&2; exit 2; }
symbol="$1"; date="$2"; json="$3"

case "$symbol" in
  ""|*[!A-Za-z0-9.-]*) echo "evidence-append: bad symbol '$symbol'" >&2; exit 2 ;;
esac
case "$date" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "evidence-append: DATE must be YYYY-MM-DD, got '$date'" >&2; exit 2 ;;
esac

req='has("claim") and has("url") and has("source_type") and has("observed") and has("independence")'
types='["end-user","employee","counterparty","enthusiast","primary-doc","mainstream"]'

if ! printf '%s' "$json" | jq -e "type == \"object\" and ($req)" >/dev/null 2>&1; then
  echo "evidence-append: JSON failed validation (required: $req)" >&2; exit 2
fi
if ! printf '%s' "$json" | jq -e --argjson types "$types" \
     '(.source_type | type) == "string" and (.source_type as $t | $types | index($t)) != null' \
     >/dev/null 2>&1; then
  echo "evidence-append: source_type must be one of $types" >&2; exit 2
fi
observed="$(printf '%s' "$json" | jq -r '.observed')"
if [ "$observed" != "$date" ]; then
  echo "evidence-append: observed ($observed) must equal DATE arg ($date)" >&2; exit 2
fi

dir="${TC_RESEARCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)/research}/evidence"
mkdir -p "$dir"
printf '%s\n' "$(printf '%s' "$json" | jq -c .)" >> "$dir/$symbol.jsonl"
echo "evidence-append: appended to evidence/$symbol.jsonl"
