#!/bin/bash
# Regression suite for status-write.sh — the §7.2 close writer.
#
# Every refusal here corresponds to a way the NEXT session misreads the file
# rather than a way this write fails. That asymmetry is the point: a status file
# that is merely ugly costs nothing, one that is missing the HWM line costs a
# self-inflicted trading halt (see the header of status-write.sh).
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2

pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# Redirect the writer at a scratch tree so a test run can never touch real
# status files — the suite must be safe to run on the live server.
mkdir -p "$tmp/scripts" "$tmp/status"
cp scripts/status-write.sh scripts/lib-lock.sh "$tmp/scripts/"
W="$tmp/scripts/status-write.sh"
D=2026-01-02

good() {
  cat <<EOF
# Session close — $D
placeholder line 2
placeholder line 3

### State recorded — current

- Account value: **\$3,789.08**
- Competition capital: **\$2,889.08**
- **High-water mark: \$2,900.00** — no ratchet, carried unchanged
- Halt \$2,320.00 · drawdown **-0.38%** · level **OK**
- Settled cash: \$2,393.57 · Unsettled: \$0.00
- Positions: none
- Cumulative option premium: **\$0.00**

## What changed
Nothing.

## Rules that bound a decision
None.
EOF
}

# Content arrives via a FILE, never a pipe. `producer | try ...` puts try in a
# subshell, so every pass/fail increment inside it is discarded and the suite
# reports a count far lower than the lines it printed. That is exactly the
# self-certifying failure these suites exist to prevent, and it shipped here
# first — 18 assertions printed, 5 counted.
IN="$tmp/in.md"
try() { # label expect_rc [flag]   (content already in $IN)
  local label="$1" want="$2" out rc
  out="$("$W" "$D" ${3:-} < "$IN" 2>&1)"; rc=$?
  if [ "$rc" -eq "$want" ]; then ok "$label"
  else bad "$label — rc=$rc want=$want ${out:+($out)}"; fi
}

echo "== the happy path =="
rm -f "$tmp/status/$D.md"
good > "$IN"; try "a complete file is accepted" 0
[ -f "$tmp/status/$D.md" ] && ok "the file lands on disk" || bad "no file written"

echo
echo "== refusals that protect the NEXT session =="
rm -f "$tmp/status/$D.md"
cat > "$IN" <<EOF
# Session close — $D

### State recorded — current

- Account value: **\$1.00**
- Competition capital: **\$1.00**
- **High-water mark: \$1.00** — carried unchanged
- Settled cash: **\$1.00**
EOF
try "a short file is refused even when every field is present" 1

rm -f "$tmp/status/$D.md"
good | sed 's/^# Session close.*/# Some other heading/' > "$IN"; try "a wrong H1 is refused" 1

rm -f "$tmp/status/$D.md"
good | sed 's/^### State recorded — current$/### State recorded — earlier/' > "$IN"
try "no state block is refused" 1

# tick.md §B5 explicitly warns that superseded blocks with near-identical
# headings are the trap. Two exact blocks make its grep pick one arbitrarily.
rm -f "$tmp/status/$D.md"
{ good; echo; echo '### State recorded — current'; echo '- stale duplicate'; } > "$IN"
try "TWO state blocks are refused (§B5 grep ambiguity)" 1

for field in 'Account value:' 'Competition capital:' 'High-water mark:' 'Settled cash:'; do
  rm -f "$tmp/status/$D.md"
  good | grep -vF "$field" > "$IN"; try "a file missing '$field' is refused" 1
done

# The ratchet decision must be stated, not implied — this is the specific
# omission that would let the mark silently freeze.
rm -f "$tmp/status/$D.md"
good | sed 's/ — no ratchet, carried unchanged//' > "$IN"
try "an HWM line that does not state the ratchet decision is refused" 1

echo
echo "== overwrite discipline =="
rm -f "$tmp/status/$D.md"
good > "$IN"; "$W" "$D" < "$IN" >/dev/null 2>&1
good > "$IN"; try "a second write without --replace is refused" 3
good > "$IN"; try "a second write WITH --replace succeeds" 0 --replace
[ -f "$tmp/status/$D.md.prev" ] && ok "--replace keeps the prior version as .prev" \
                                 || bad "--replace did not preserve .prev"

echo
echo "== argument validation =="
out="$("$W" 2026-1-2 < "$IN" 2>&1)"; [ "$?" -eq 2 ] \
  && ok "a malformed DATE exits 2" || bad "malformed DATE should exit 2 ($out)"
out="$("$W" < "$IN" 2>&1)"; [ "$?" -eq 2 ] \
  && ok "a missing DATE exits 2" || bad "missing DATE should exit 2"
out="$("$W" "$D" --clobber < "$IN" 2>&1)"; [ "$?" -eq 2 ] \
  && ok "an unknown flag exits 2 rather than being ignored" || bad "unknown flag should exit 2"

echo
echo "-------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
