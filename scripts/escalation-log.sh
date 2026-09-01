#!/bin/bash
# The escalation prediction ledger.
#
#   scripts/escalation-log.sh raise SYMBOL DATE 'JSON'
#   scripts/escalation-log.sh score ID right|wrong|void
#
# A prediction recorded BEFORE the outcome is evidence; one reconstructed after
# is a story. direction and event_date are therefore mandatory at raise time,
# and a raise carrying fewer than two independent source_types is refused —
# two source types IS the escalation bar, and a writer that accepts one lets
# the bar rot with no trace in the ledger it produces.
#
# The id is symbol-date-CRC32 over the CANONICALISED claim (jq -S), so the same
# prediction hashes the same however it was formatted, and a re-raise of it is
# refused rather than appended. One prediction must be one row: a scheduled job
# re-run that doubled a row would double-count it in the hit rate this file
# exists to make measurable.
#
# Scoring is append-only, per CLAUDE.md §7.1: a correction is a new row, never a
# rewrite. So an id may carry more than one score row and the LAST one wins —
# any hit-rate reader must reduce to the latest score per id, not count rows.
set -euo pipefail
dir="${TC_RESEARCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)/research}"
mkdir -p "$dir"
file="$dir/escalations.jsonl"
mode="${1:-}"

is_date() { [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; }

case "$mode" in
  raise)
    [ "$#" -eq 4 ] || { echo "escalation-log: raise SYMBOL DATE JSON" >&2; exit 2; }
    symbol="$2"; date="$3"; json="$4"
    case "$symbol" in *[!A-Za-z0-9.-]*|"") echo "escalation-log: bad symbol '$symbol'" >&2; exit 2 ;; esac
    is_date "$date" || { echo "escalation-log: DATE must be YYYY-MM-DD, got '$date'" >&2; exit 2; }
    req='has("claim") and has("direction") and has("event_date") and has("source_types")'
    printf '%s' "$json" | jq -e "type==\"object\" and ($req)" >/dev/null 2>&1 \
      || { echo "escalation-log: missing required field" >&2; exit 2; }
    d="$(printf '%s' "$json" | jq -r .direction)"
    case "$d" in up|down) ;; *) echo "escalation-log: direction must be up|down" >&2; exit 2 ;; esac
    ev="$(printf '%s' "$json" | jq -r .event_date)"
    is_date "$ev" || { echo "escalation-log: event_date must be YYYY-MM-DD, got '$ev'" >&2; exit 2; }
    # Type first, then length: `"abc" | length` is 3, so a bare string would
    # otherwise clear a two-source bar on its character count alone.
    printf '%s' "$json" | jq -e '.source_types | type == "array"' >/dev/null 2>&1 \
      || { echo "escalation-log: source_types must be a JSON array" >&2; exit 2; }
    n="$(printf '%s' "$json" | jq -r '.source_types | length')"
    [ "$n" -ge 2 ] || { echo "escalation-log: need 2+ source types, got $n" >&2; exit 2; }
    id="$symbol-$date-$(printf '%s' "$json" | jq -Sc . | cksum | awk '{print $1}')"
    if [ -f "$file" ] && [ "$(jq -s --arg i "$id" \
         '[.[] | select(.kind=="raise" and .id==$i)] | length' "$file" 2>/dev/null || echo 0)" -gt 0 ]; then
      echo "escalation-log: $id already raised; one prediction is one row" >&2; exit 2
    fi
    printf '%s' "$json" | jq -c --arg i "$id" --arg s "$symbol" --arg d "$date" \
      '. + {id:$i, symbol:$s, raised:$d, kind:"raise"}' >> "$file"
    echo "escalation-log: raised $id"
    ;;
  score)
    [ "$#" -eq 3 ] || { echo "escalation-log: score ID OUTCOME" >&2; exit 2; }
    id="$2"; outcome="$3"
    case "$outcome" in right|wrong|void) ;; *) echo "escalation-log: bad outcome" >&2; exit 2 ;; esac
    # Matched as a literal, never as a pattern: ids carry '.' because tickers
    # do (BRK.B), and a regex lookup would attach this outcome to BRKXB's
    # prediction — a hit rate scored against a claim nobody made.
    [ -f "$file" ] || { echo "escalation-log: no ledger at $file" >&2; exit 2; }
    [ "$(jq -s --arg i "$id" '[.[] | select(.kind=="raise" and .id==$i)] | length' \
         "$file" 2>/dev/null || echo 0)" -gt 0 ] \
      || { echo "escalation-log: no such id" >&2; exit 2; }
    jq -nc --arg i "$id" --arg o "$outcome" \
      --arg t "$(TZ=America/New_York date +%Y-%m-%d)" \
      '{id:$i, outcome:$o, scored:$t, kind:"score"}' >> "$file"
    echo "escalation-log: scored $id $outcome"
    ;;
  *) echo "escalation-log: mode must be raise|score" >&2; exit 2 ;;
esac
