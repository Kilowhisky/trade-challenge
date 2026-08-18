#!/bin/bash
# Regression suite for scripts/universe-filter.sh
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.." || exit 2
F=scripts/universe-filter.sh
FIX=tests/fixtures/verbose-sample.txt
pass=0; fail=0
ok()  { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
no()  { fail=$((fail+1)); printf 'FAIL  %s\n       %s\n' "$1" "${2:-}"; }

OUT=$(mktemp); trap 'rm -f "$OUT"' EXIT

echo "== parsing =="
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>&1
n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "produces rows from a verbose payload" || no "produces rows from a verbose payload" "got $n rows"

grep -q '^symbol	price	adv10' "$OUT" && ok "emits the TSV header" || no "emits the TSV header"

echo
echo "-------------------------------------------"
printf '%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
