#!/bin/bash
# Regression suite for evidence-append.sh.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
TMPROOT="$(mktemp -d)"
# Nested on purpose: the traversal case below lands inside TMPROOT, so it is
# both observable and cleaned up.
export TC_RESEARCH_DIR="$TMPROOT/a/b/research"
trap 'rm -rf "$TMPROOT"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

# rc is captured explicitly rather than read from $? after the fact, and a
# refusal must be the documented usage-error status 2 -- not merely "non-zero",
# which an absent or crashing script also satisfies.
run() { rc=0; ./scripts/evidence-append.sh "$1" "$2" "$3" >/dev/null 2>&1 || rc=$?; }
lines() { if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi; }

D=2026-09-01
F="$TC_RESEARCH_DIR/evidence/ROKU.jsonl"
V='{"claim":"40% of reviews since July cite app crashes","url":"https://example.com/a","source_type":"end-user","observed":"2026-09-01","independence":"user reviews, not derived from any press release"}'

run ROKU "$D" "$V"
[ "$rc" = 0 ] && [ "$(lines "$F")" = 1 ] \
  && ok "appends a valid record" || bad "no ledger record written (rc=$rc, lines=$(lines "$F"))"

run ROKU "$D" "$V"
n="$(lines "$F")"
[ "$rc" = 0 ] && [ "$n" = 2 ] && ok "appends rather than overwrites" || bad "ledger has $n lines, want 2"

# Each refusal below asserts BOTH the refusal status and that the ledger did not
# grow: a writer that appends first and validates afterward is not a validator.
no_claim='{"url":"https://e.com","source_type":"end-user","observed":"2026-09-01","independence":"x"}'
run ROKU "$D" "$no_claim"
[ "$rc" = 2 ] && [ "$(lines "$F")" = 2 ] \
  && ok "refuses a record with no claim" || bad "accepted a claimless record (rc=$rc, lines=$(lines "$F"))"

bad_type='{"claim":"c","url":"https://e.com","source_type":"blogpost","observed":"2026-09-01","independence":"x"}'
run ROKU "$D" "$bad_type"
[ "$rc" = 2 ] && [ "$(lines "$F")" = 2 ] \
  && ok "refuses an unknown source_type" || bad "accepted source_type 'blogpost' (rc=$rc, lines=$(lines "$F"))"

# The baseline is only trustworthy if observation dates are. A record whose
# observed date disagrees with the pass date silently corrupts every later
# delta computed against it.
skewed='{"claim":"c","url":"https://e.com","source_type":"end-user","observed":"2026-08-01","independence":"x"}'
run ROKU "$D" "$skewed"
[ "$rc" = 2 ] && [ "$(lines "$F")" = 2 ] \
  && ok "refuses observed != DATE argument" || bad "accepted a skewed observation date (rc=$rc, lines=$(lines "$F"))"

# The traversal target's parent is pre-created on purpose: without it the write
# would fail on a missing directory even with the guard removed, and the
# assertion would be proving an accident of layout rather than the guard.
TRAV="$TMPROOT/a/b/etc/x.jsonl"
mkdir -p "$(dirname "$TRAV")"
run "../../etc/x" "$D" "$V"
[ "$rc" = 2 ] && [ ! -e "$TRAV" ] \
  && ok "refuses a symbol containing a path" || bad "accepted a traversal symbol (rc=$rc, wrote=$([ -e "$TRAV" ] && echo yes || echo no))"

# mainstream is a LEGAL type on purpose: recording that a story reached
# mainstream coverage is how the kill switch gets its evidence.
ms='{"claim":"c","url":"https://e.com","source_type":"mainstream","observed":"2026-09-01","independence":"x"}'
run ROKU "$D" "$ms"
[ "$rc" = 0 ] && [ "$(lines "$F")" = 3 ] \
  && ok "accepts source_type mainstream (kill-switch evidence)" \
  || bad "refused mainstream, so the kill switch can never be recorded (rc=$rc, lines=$(lines "$F"))"

# jq's `index` does SUBSEQUENCE search when handed an array, so a naive
# membership test reports ["end-user"] as a member of the valid-types list and
# waves it through. That would put an array where every reader expects a
# string. The guard is the explicit (.source_type|type)=="string" clause, and
# this is the assertion that stops it being removed as redundant.
arr='{"claim":"c","url":"https://e.com","source_type":["end-user"],"observed":"2026-09-01","independence":"x"}'
before=$(cat "$F" 2>/dev/null | wc -l | tr -d ' ')
rc=0; ./scripts/evidence-append.sh ROKU "$D" "$arr" >/dev/null 2>&1 || rc=$?
after=$(cat "$F" 2>/dev/null | wc -l | tr -d ' ')
if [ "$rc" = "2" ] && [ "$before" = "$after" ]; then
  ok "refuses an ARRAY source_type (jq index subsequence trap)"
else
  bad "array source_type: rc=$rc (want 2), lines $before->$after (want unchanged)"
fi

# Adversarial regression: has() is TRUE for an explicit null, so a key-presence
# guard accepted a record whose every field was null — in the one file whose
# value is that its claims can be checked later.
rc2() { local rc=0; ./scripts/evidence-append.sh "$@" >/dev/null 2>&1 || rc=$?; printf '%s' "$rc"; }
[ "$(rc2 ROKU "$D" '{"claim":null,"url":null,"source_type":"end-user","observed":"2026-09-01","independence":null}')" = "2" ] \
  && ok "null-valued required fields are refused" || bad "a record of all nulls was appended"
[ "$(rc2 ROKU "$D" '{"claim":0,"url":{"a":1},"source_type":"end-user","observed":"2026-09-01","independence":[]}')" = "2" ] \
  && ok "wrong-typed required fields are refused" || bad "a number claim and object url were appended"
[ "$(rc2 ROKU "$D" '{"claim":"","url":"https://e.com","source_type":"end-user","observed":"2026-09-01","independence":"x"}')" = "2" ] \
  && ok "an empty-string claim is refused" || bad "an empty claim was appended"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
