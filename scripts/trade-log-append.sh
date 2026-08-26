#!/bin/bash
# Append one row to trade-log.csv. The §4.9 pre-trade check goes in through
# here, BEFORE the order — never reconstructed afterward.
#
# Why a script rather than an editor: the trader agent has no Write or Edit
# tools, by construction, exactly as every other agent in this system. Its file
# writes go through whitelisted append-only scripts so the harness enforces
# §7.1's "entries are never edited once written" rather than the instruction
# hoping for it. A correction is a NEW ROW, never a rewrite.
#
# §7.1 is blunt about what this log now is: local-only, append-only by
# convention, on one machine, with no external witness since the history purge.
# The sidecar store is that witness again — but only for rows that exist. A row
# written after the fill is not a gate, it is a note.
#
# Usage — every field is explicit and positional; there are no defaults,
# because a defaulted number in a risk log is a number nobody chose:
#
#   scripts/trade-log-append.sh \
#     DATE TIME_ET ACTION SYMBOL INSTRUMENT QUANTITY LIMIT_PRICE FILL_PRICE \
#     GROSS FEES NET PCT_OF_COMP_AFTER STOP_TRIGGER STOP_LIMIT \
#     SETTLED_BEFORE ACCOUNT_VALUE_AFTER RESERVE COMP_AFTER HWM DRAWDOWN_PCT \
#     CUM_OPTION_PREMIUM RULE_CHECK 'RATIONALE text'
#
# Use "-" for any field genuinely not yet known (a fill price on a pre-trade
# row). Do not invent a value to fill a column.
#
# DATE and TIME_ET come from get_datetime (Eastern), never this machine — the
# laptop runs Pacific and the server's container is ET, and a log that files a
# 09:35 ET order under the previous day is worse than no log.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
log="$repo_root/trade-log.csv"

# Test seam. The happy path cannot be exercised against the real log without
# putting a fabricated row in the audit trail, which §7.3 would then require
# reporting as a defect. Redirecting is therefore allowed — and announced on
# stderr every time, because a silent redirect of the audit log is precisely
# the capability an audit log must not have.
if [ -n "${TC_TRADE_LOG:-}" ]; then
  log="$TC_TRADE_LOG"
  echo "trade-log-append: WARNING writing to TC_TRADE_LOG=$log, not the real trade log" >&2
fi

want=23
if [ "$#" -lt "$want" ]; then
  echo "trade-log-append: expected $want fields, got $#" >&2
  echo "trade-log-append: see the usage header — no field defaults, use '-' for genuinely unknown" >&2
  exit 2
fi

case "$1" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "trade-log-append: DATE must be YYYY-MM-DD, got '$1'" >&2; exit 2 ;;
esac
case "$2" in
  [0-9][0-9]:[0-9][0-9]) ;;
  *) echo "trade-log-append: TIME_ET must be HH:MM, got '$2'" >&2; exit 2 ;;
esac

[ -f "$log" ] || { echo "trade-log-append: $log does not exist — refusing to create it" >&2; exit 2; }

# CSV-quote every field: rationales contain commas, and a rationale that eats
# the next column silently corrupts the audit trail rather than failing loudly.
csv_quote() {
  local v="$1"
  v="${v//[$'\t\r\n']/ }"
  v="${v//\"/\"\"}"
  printf '"%s"' "$v"
}

fields=("$@")
# Everything from field 23 onward is the rationale; join it back together so an
# unquoted call site loses the text's spacing rather than its columns.
if [ "$#" -gt "$want" ]; then
  echo "trade-log-append: RATIONALE arrived as multiple words — quote it at the call site" >&2
  rationale="${fields[*]:$((want-1))}"
  fields=("${fields[@]:0:$((want-1))}")
  fields+=("$rationale")
fi

row=""
for f in "${fields[@]}"; do
  [ -n "$row" ] && row+=","
  row+="$(csv_quote "$f")"
done

# A trailing newline may be missing if the file was last touched by hand.
[ -s "$log" ] && [ "$(tail -c1 "$log" | wc -l | tr -d ' ')" = "0" ] && printf '\n' >> "$log"

printf '%s\n' "$row" >> "$log"
echo "trade-log-append: appended $1 $2 $3 $4"
