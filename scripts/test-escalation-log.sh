#!/bin/bash
# Regression suite for escalation-log.sh — the prediction ledger.
#
# Every refusal here defends the same property: a prediction recorded BEFORE
# the outcome is evidence, one reconstructed afterward is a story. So the
# falsifiable parts (direction, event_date) are mandatory at raise time, and
# the escalation bar itself — two independent source types — is enforced by
# the writer rather than by the discipline of whoever calls it. A writer that
# accepts one source lets the bar rot silently, and a rotted bar is invisible
# in the ledger it produces.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
TC_RESEARCH_DIR="$(mktemp -d)"; export TC_RESEARCH_DIR
trap 'rm -rf "$TC_RESEARCH_DIR"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
F="$TC_RESEARCH_DIR/escalations.jsonl"
V='{"claim":"fuel hedged 18mo forward","direction":"up","event_date":"2026-10-15","source_types":["counterparty","enthusiast"]}'

./scripts/escalation-log.sh raise DAL 2026-09-01 "$V" >/dev/null 2>&1
[ -f "$F" ] && ok "raise appends an escalation" || bad "no escalations file"

# Not vacuous by design: the assertion above passes on any byte written to the
# path. This one is what makes the row a record — without id/symbol/raised the
# ledger cannot be scored, grouped, or dated, and the claim must survive intact.
r="$(head -1 "$F" 2>/dev/null | jq -r '[.kind, .symbol, .raised, (.id|length>0), .claim] | @tsv' 2>/dev/null)"
[ "$r" = "$(printf 'raise\tDAL\t2026-09-01\ttrue\tfuel hedged 18mo forward')" ] \
  && ok "raised row carries kind/symbol/raised/id and preserves the claim" \
  || bad "raised row is not a scorable record: got '$r'"

# A raise with fewer than two source types must be refused: the escalation bar
# IS two independent types, and a writer that accepts one lets the bar rot.
one='{"claim":"c","direction":"up","event_date":"2026-10-15","source_types":["end-user"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$one" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a raise with only one source type" \
  || bad "accepted a single-source escalation, defeating the bar"

# The same bar, one level down: a bare string has a length too, and `length>=2`
# waves "counterparty" through as if it were two independent sources.
str='{"claim":"c","direction":"up","event_date":"2026-10-15","source_types":"counterparty"}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$str" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses source_types that is a string, not an array" \
  || bad "counted the characters of a string as source types"

# claim is the only required field with no second guard downstream: direction,
# event_date and source_types each have their own validator, so a missing claim
# is caught here or nowhere, and an escalation with no claim asserts nothing.
noclaim='{"direction":"up","event_date":"2026-10-15","source_types":["end-user","employee"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$noclaim" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a raise with no claim" || bad "accepted an escalation asserting nothing"

nodir='{"claim":"c","event_date":"2026-10-15","source_types":["end-user","employee"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$nodir" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a raise with no direction" \
  || bad "accepted an unfalsifiable escalation with no predicted direction"

baddir='{"claim":"c","direction":"sideways","event_date":"2026-10-15","source_types":["end-user","employee"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$baddir" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a direction outside up|down" || bad "accepted direction 'sideways'"

# An unparseable event_date is an unfalsifiable one: nothing can later decide
# whether the catalyst has happened yet, so the prediction can never be scored.
badev='{"claim":"c","direction":"up","event_date":"next quarter","source_types":["end-user","employee"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$badev" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses an event_date that is not YYYY-MM-DD" || bad "accepted event_date 'next quarter'"

./scripts/escalation-log.sh raise DAL 09/01/2026 "$V" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a DATE arg that is not YYYY-MM-DD" || bad "accepted DATE '09/01/2026'"

# The id is a hash of the canonicalised claim, so a re-run of a scheduled job
# re-raising the same prediction is the same row, not a second data point. Two
# rows for one prediction silently double-count it in the hit rate this file
# exists to produce. Same content, different key order and whitespace:
dup='{ "source_types": ["counterparty","enthusiast"], "event_date": "2026-10-15", "direction": "up", "claim": "fuel hedged 18mo forward" }'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$dup" >/dev/null 2>&1
rc=$?
n_dal="$(grep -c '"symbol":"DAL"' "$F" 2>/dev/null || true)"; n_dal="${n_dal:-0}"
{ [ "$rc" -ne 0 ] && [ "$n_dal" -eq 1 ]; } \
  && ok "refuses a duplicate raise regardless of key order or whitespace" \
  || bad "duplicate raise double-counted: rc=$rc rows=$n_dal"

id="$(head -1 "$F" | jq -r .id)"
./scripts/escalation-log.sh score "$id" right >/dev/null 2>&1
grep -q '"outcome":"right"' "$F" && ok "score appends an outcome" || bad "score did not record"

# Ids contain '.' because tickers do (BRK.B). Looked up as a regex rather than
# as a literal, 'BRK.B-...' matches the stored 'BRKXB-...' and an outcome gets
# attached to a prediction nobody made.
V2='{"claim":"buyback authorised","direction":"up","event_date":"2026-10-20","source_types":["employee","counterparty"]}'
./scripts/escalation-log.sh raise BRKXB 2026-09-01 "$V2" >/dev/null 2>&1
stored="$(jq -r 'select(.symbol=="BRKXB") | .id' "$F" 2>/dev/null | head -1)"
probe="BRK.B-${stored#BRKXB-}"
./scripts/escalation-log.sh score "$probe" right >/dev/null 2>&1
rc=$?
n_probe="$(grep -cF "\"id\":\"$probe\"" "$F" 2>/dev/null || true)"; n_probe="${n_probe:-0}"
{ [ -n "$stored" ] && [ "$rc" -ne 0 ] && [ "$n_probe" -eq 0 ]; } \
  && ok "score refuses an id that only matches as a regex" \
  || bad "scored a nonexistent id via regex match: stored='$stored' rc=$rc rows=$n_probe"

./scripts/escalation-log.sh score "$id" maybe >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses an outcome outside right|wrong|void" || bad "accepted outcome 'maybe'"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
