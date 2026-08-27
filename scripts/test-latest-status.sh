#!/bin/bash
# Regression suite for latest-status.sh.
#
# The interesting cases are all about picking the WRONG figure rather than
# failing: a superseded state block, a lexical-vs-mtime ordering difference, a
# --before boundary that includes the day it should exclude. Each of those
# returns a plausible number, and a plausible wrong high-water mark is how the
# §3.6 halt threshold ends up in the wrong place.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2

pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/scripts" "$tmp/status"
cp scripts/latest-status.sh "$tmp/scripts/"
S="$tmp/scripts/latest-status.sh"

mk() { # DATE HWM  -> a minimal but well-formed status file
  cat > "$tmp/status/$1.md" <<EOF
# Session close — $1

### State recorded — current

- Account value: **\$3,000.00**
- Competition capital: **\$2,000.00**
- **High-water mark: $2** — carried unchanged
- Settled cash: **\$1.00**
EOF
}

eq() { # label got want
  [ "$2" = "$3" ] && ok "$1" || bad "$1 — got '$2' want '$3'"
}

echo "== picking the latest file =="
mk 2026-08-24 '$2,800.00'; mk 2026-08-25 '$2,900.00'; mk 2026-09-02 '$3,100.00'
eq "returns the newest by date" "$("$S")" "status/2026-09-02.md"
eq "reads that file's HWM"      "$("$S" --hwm)" '$3,100.00'

# Dates sort lexically here, but mtime does not have to agree: a git checkout
# or a sidecar sync rewrites mtimes in arbitrary order. Ordering by mtime would
# silently pick an old file.
touch -t 203001010000 "$tmp/status/2026-08-24.md"
eq "date order wins over mtime order" "$("$S")" "status/2026-09-02.md"

echo
echo "== --before, the case the ratchet depends on =="
# session-close resolves the PRIOR mark. Including today's own file would make
# the ratchet compare the day against itself and never move.
eq "excludes the boundary date itself" "$("$S" --before 2026-09-02)" "status/2026-08-25.md"
eq "and reads the prior mark"          "$("$S" --before 2026-09-02 --hwm)" '$2,900.00'
eq "skips back over gaps"              "$("$S" --before 2026-08-25)" "status/2026-08-24.md"

"$S" --before 2026-08-24 >/dev/null 2>&1
[ "$?" -eq 1 ] && ok "no earlier file exits 1 rather than printing nothing" \
               || bad "no earlier file should exit 1"

echo
echo "== the superseded-block trap (tick.md §B5) =="
# §B5 warns that a status file may carry superseded blocks with near-identical
# headings and that the FIRST 'High-water mark' hit can be the stale one. A
# naive grep reads $9,999.00 here.
cat > "$tmp/status/2026-09-03.md" <<'EOF'
# Session close — 2026-09-03

### State recorded — earlier (superseded, kept for the record)

- **High-water mark: $9,999.00** — this block is stale

### State recorded — current

- Account value: **$3,000.00**
- Competition capital: **$2,000.00**
- **High-water mark: $3,200.00** — ratcheted
- Settled cash: **$1.00**
EOF
eq "reads only the 'current' block" "$("$S" --hwm)" '$3,200.00'

# The same trap in the other order: a superseded block placed AFTER the
# current one must not be reached.
cat > "$tmp/status/2026-09-04.md" <<'EOF'
# Session close — 2026-09-04

### State recorded — current

- Account value: **$3,000.00**
- Competition capital: **$2,000.00**
- **High-water mark: $3,300.00** — ratcheted
- Settled cash: **$1.00**

### State recorded — earlier (superseded)

- **High-water mark: $1.00** — stale
EOF
eq "stops at the next heading" "$("$S" --hwm)" '$3,300.00'

echo
echo "== failure modes =="
cat > "$tmp/status/2026-09-05.md" <<'EOF'
# Session close — 2026-09-05

### State recorded — current

- Account value: **$3,000.00**
- Settled cash: **$1.00**

### State recorded — earlier (superseded)

- **High-water mark: $8,888.00** — stale, and BELOW the current block
EOF
"$S" --hwm >/dev/null 2>&1
[ "$?" -eq 1 ] && ok "a current block with no HWM does not fall through to a later block" \
               || bad "missing HWM should exit 1, not borrow a superseded figure"
eq "and it certainly does not return the stale figure" "$("$S" --hwm 2>/dev/null)" ""
rm "$tmp/status/2026-09-05.md"

"$S" --before 2026-8-1 >/dev/null 2>&1
[ "$?" -eq 2 ] && ok "a malformed --before DATE exits 2" || bad "malformed date should exit 2"
"$S" --nonsense >/dev/null 2>&1
[ "$?" -eq 2 ] && ok "an unknown flag exits 2 rather than being ignored" || bad "unknown flag should exit 2"

rm -f "$tmp"/status/*.md
"$S" >/dev/null 2>&1
[ "$?" -eq 1 ] && ok "an empty status/ exits 1" || bad "empty status/ should exit 1"

echo
echo "-------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
