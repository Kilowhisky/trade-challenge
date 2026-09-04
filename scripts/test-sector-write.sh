#!/bin/bash
# Regression suite for sector-write.sh.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
export TC_RESEARCH_DIR="$(mktemp -d)"
trap 'rm -rf "$TC_RESEARCH_DIR"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

F="$TC_RESEARCH_DIR/sectors.tsv"

./scripts/sector-write.sh ROKU consumer-software >/dev/null 2>&1
grep -q "^ROKU	consumer-software	" "$F" 2>/dev/null \
  && ok "writes a tagged symbol" || bad "did not write ROKU"

# An unknown sector must be REFUSED, not silently stored: a typo that becomes a
# new sector value silently shrinks the universe the scout looks at.
./scripts/sector-write.sh AAPL consumer-sofware >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "rejects an invalid sector" || bad "accepted a misspelled sector"
grep -q "consumer-sofware" "$F" 2>/dev/null \
  && bad "wrote the invalid sector anyway" || ok "invalid sector not persisted"

# Re-tagging must UPDATE, not duplicate: the weekly refresh re-runs over names
# already tagged, and duplicate rows would double-count the universe.
./scripts/sector-write.sh ROKU other >/dev/null 2>&1
# Counted with awk, not `grep -c ... || echo 0`: on a no-match grep prints 0 AND
# exits 1, so the fallback appends a second 0 and the failure message reads
# "ROKU has 0\n0 rows".
n="$(awk -F'	' '$1=="ROKU"{c++} END{print c+0}' "$F" 2>/dev/null)"
[ "$n" = "1" ] && ok "re-tagging updates in place" || bad "ROKU has $n rows, want 1"
# Checked on the FIRST ROKU row, not with a bare grep: a grep for the new value
# still matches when a stale row is left sitting above it, so it passed even with
# the de-dupe removed. Reading row 1 fails on both a missed update and a duplicate.
v="$(awk -F'	' '$1=="ROKU"{printf "%s|%s", $2, ($3 ~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/ ? "dated" : "undated"); exit}' "$F" 2>/dev/null)"
[ "$v" = "other|dated" ] && ok "re-tag took the new value" \
  || bad "first ROKU row reads '$v', want 'other|dated'"

./scripts/sector-write.sh "RO KU" consumer-software >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "rejects a symbol containing whitespace" \
  || bad "accepted a whitespace symbol, which would shift TSV columns"

# DATE is explicit so a caller can pass the get_datetime-derived Eastern date
# rather than trusting this host's clock — the contract every other writer in
# this tree uses. Without it the ledger date is untestable and drifts with the
# machine.
./scripts/sector-write.sh DAL airlines-transport 2026-01-15 >/dev/null 2>&1
awk -F'\t' '$1=="DAL" && $3=="2026-01-15"{f=1} END{exit !f}' "$F" 2>/dev/null \
  && ok "an explicit DATE is stored verbatim" \
  || bad "explicit DATE was ignored; the tag date cannot be pinned"

./scripts/sector-write.sh DAL airlines-transport 15-01-2026 >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "rejects a malformed DATE" || bad "accepted DATE '15-01-2026'"

./scripts/sector-write.sh DAL airlines-transport 2026-01-15 extra >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "rejects a fourth argument" || bad "accepted an unexpected 4th arg"


echo "== --batch: the weekly tagger's write path =="
# ~3,000 qualified names cannot be tagged one Bash call at a time, and the
# container's gate accepts exactly one multi-line form: a heredoc on a bare
# script path. So the batch is the production path and the single call is the
# interactive one; both must land identically.
./scripts/sector-write.sh --batch 2026-02-01 >/dev/null 2>&1 <<'EOF'
# comment lines and blanks are ignored

UAL airlines-transport
NFLX	consumer-software
ROKU semis-hardware
EOF
[ "$?" -eq 0 ] && ok "batch of three exits 0" || bad "batch exited non-zero"
awk -F'\t' '$1=="UAL"&&$2=="airlines-transport"&&$3=="2026-02-01"{f=1} END{exit !f}' "$F" \
  && ok "batch row stored with the explicit DATE" || bad "UAL row missing or undated"
awk -F'\t' '$1=="NFLX"&&$2=="consumer-software"{f=1} END{exit !f}' "$F" \
  && ok "tab-separated batch line accepted" || bad "NFLX (tab-separated) not stored"
n="$(awk -F'\t' '$1=="ROKU"{c++} END{print c+0}' "$F")"
v="$(awk -F'\t' '$1=="ROKU"{print $2; exit}' "$F")"
[ "$n" = "1" ] && [ "$v" = "semis-hardware" ] \
  && ok "batch re-tag of an existing symbol updates in place" \
  || bad "ROKU has $n row(s) reading '$v' after a batch re-tag; want 1 row, semis-hardware"

# All-or-nothing: a bad line anywhere refuses the WHOLE batch and leaves the
# file byte-identical. 199 good rows plus one corrupt one is how the universe
# gets a silent hole in it.
before="$(cat "$F")"
./scripts/sector-write.sh --batch 2026-02-01 >/dev/null 2>"$TC_RESEARCH_DIR/err" <<'EOF'
LUV airlines-transport
AAPL semis-hardwear
JBLU airlines-transport
EOF
rc=$?
[ "$rc" -ne 0 ] && ok "a batch with a misspelled sector is refused (exit $rc)" \
  || bad "batch with a bad sector exited 0"
[ "$(cat "$F")" = "$before" ] && ok "a refused batch leaves the file untouched" \
  || bad "refused batch still modified sectors.tsv"
grep -q 'line 2' "$TC_RESEARCH_DIR/err" && ok "refusal names the offending line" \
  || bad "refusal did not name the line: $(cat "$TC_RESEARCH_DIR/err")"

./scripts/sector-write.sh --batch 2026-02-01 >/dev/null 2>&1 <<'EOF'
LUV airlines-transport extra-field
EOF
[ "$?" -ne 0 ] && ok "a three-field batch line is refused" || bad "accepted a 3-field line"

./scripts/sector-write.sh --batch 2026-02-01 >/dev/null 2>&1 </dev/null
[ "$?" -ne 0 ] && ok "an empty batch is refused" || bad "empty batch exited 0"

./scripts/sector-write.sh --batch 31-02-2026 >/dev/null 2>&1 <<'EOF'
LUV airlines-transport
EOF
[ "$?" -ne 0 ] && ok "batch rejects a malformed DATE" || bad "batch accepted DATE '31-02-2026'"

# A batch naming one symbol twice keeps the LAST line — the same result two
# single calls in that order would produce.
./scripts/sector-write.sh --batch 2026-02-02 >/dev/null 2>&1 <<'EOF'
DAL other
DAL airlines-transport
EOF
n="$(awk -F'\t' '$1=="DAL"{c++} END{print c+0}' "$F")"
v="$(awk -F'\t' '$1=="DAL"{print $2; exit}' "$F")"
[ "$n" = "1" ] && [ "$v" = "airlines-transport" ] \
  && ok "a duplicated symbol in one batch keeps the last line, once" \
  || bad "DAL has $n row(s) reading '$v'"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
