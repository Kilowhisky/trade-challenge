# Deep-Research System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deep-research system (screener channel, ETF track, scorecard, options roster, pre-open/post-close cron agent) exactly as specced in rev 2, with the concurrency and ownership fixes landing before anything that could race.

**Architecture:** A new read-only `deep-research` agent (sibling of `research-scout`) dispatched by two harness cron entries (8:15 ET file-only pre-open run; 16:20 ET post-close run that owns the POST window). All file writes go through validating shell scripts with mkdir-based locking (macOS has no `flock(1)`) and compare-and-swap on `candidates.md`. External data via FMP (spike-gated) with a mandatory no-API fallback.

**Tech Stack:** bash + jq (scripts), markdown command/agent definitions (Claude Code harness), harness CronCreate for scheduling, Schwab MCP (read-only tools), FMP/Finnhub REST via curl/WebFetch.

**Spec:** `docs/superpowers/specs/2026-08-14-deep-research-design.md` (rev 2 — read it first; every task cites its sections).

## Global Constraints

- **Ordering is load-bearing (spec §10.3/§10.4):** script upgrades (Tasks 1–4) land before any agent that could race them; the research.md §A.4 amendment ships in the SAME commit as cron installation (Task 12); `get_movers` is not retired until the spike verdict exists (Task 7).
- The deep-research agent gets **no order tools, no account tools, no Write/Edit** (spec §8.3). Writes only via the whitelisted scripts.
- `research/candidates.md` remains the **single promotion path**; new names enter at WATCH, never HOT in the pass found; **nothing written is ever a source of order parameters** (spec §3.3).
- Pre-open run is **file-only**: zero pings, zero HOT promotions; HOT requires an RTH-timestamped quote (spec §7.1).
- Budgets are ceilings with the §8.4 priority order; every run logs `skipped: <features>` when starved.
- Failure mode: quiet — log to events corpus and stop; never ALERT.md, never interrupt the monitoring loop.
- Platform is **macOS/darwin**: no `flock(1)`; use the mkdir lock in `scripts/lib-lock.sh`. `jq` is available (existing scripts depend on it).
- All timestamps in research files are **ET** (from `get_datetime`, never the machine clock).
- Script tests run in a **scratch copy** (copy `scripts/` into a temp dir with fixture `research/` beside it — `repo_root` resolves relative to the script's own location, so a copied tree is fully isolated). Never test against the real `research/` files.
- Test scratch root for this plan: `/private/tmp/claude-501/-Users-chris-Documents-Projects-trade-challenge/8b45bad6-1080-46b2-ad82-083aa8b6ed02/scratchpad/deeptest`

---

### Task 1: Locking library + concurrency guards in research-write.sh

**Files:**
- Create: `scripts/lib-lock.sh`
- Modify: `scripts/research-write.sh`
- Test: scratch-copy run (see Global Constraints)

**Interfaces:**
- Produces: `acquire_lock <target-file> [timeout_s]` / `release_lock` (sourced functions; lock dir is `<target-file>.lock.d`); `research-write.sh [--expect-last-pass 'Last pass: <value>'] < content` — exit 0 success, 1 validation refusal, 3 CAS refusal, 5 lock timeout. Callers pass the FULL `Last pass:` line they read at compose time.
- Consumes: nothing.

- [ ] **Step 1: Write `scripts/lib-lock.sh`**

```bash
#!/bin/bash
# mkdir-based advisory lock — macOS ships no flock(1). Source, don't execute.
#   acquire_lock <target-file> [timeout_s=10]   creates <target-file>.lock.d
#   release_lock                                 removes it (also runs on EXIT)
# One lock per process; acquiring sets a trap that releases on EXIT/INT/TERM.
acquire_lock() {
  local target="$1" timeout="${2:-10}" waited=0
  _LOCKDIR="${target}.lock.d"
  while ! mkdir "$_LOCKDIR" 2>/dev/null; do
    if [ "$waited" -ge "$timeout" ]; then
      echo "lock: timed out after ${timeout}s waiting on $_LOCKDIR" >&2
      _LOCKDIR=""
      return 1
    fi
    sleep 1; waited=$((waited + 1))
  done
  trap 'release_lock' EXIT INT TERM
  return 0
}
release_lock() {
  if [ -n "${_LOCKDIR:-}" ]; then rmdir "$_LOCKDIR" 2>/dev/null || true; _LOCKDIR=""; fi
}
```

- [ ] **Step 2: Modify `research-write.sh`** — replace the argument check (lines 25–28) and the final write block (lines 57–62) so the whole file becomes:

```bash
#!/bin/bash
# Replace research/candidates.md from stdin. The ONLY sanctioned write path
# for candidates.md (spec: 2026-08-14-research-loop-design.md §3;
# concurrency: 2026-08-14-deep-research-design.md §8.2).
#
# Usage:
#   scripts/research-write.sh [--expect-last-pass 'Last pass: <value>'] <<'EOF'
#   ...full new contents of candidates.md...
#   EOF
#
# --expect-last-pass: optimistic concurrency. Pass the FULL "Last pass:" line
#   you read at compose time. If the file's current line differs, the write is
#   REFUSED (exit 3): another writer landed while you composed. Re-read the
#   file, merge your changes onto the fresh copy, and try again. Never retry
#   with your stale copy. All routine callers (scout, deep-research) MUST use
#   this flag; omitting it is for interactive/manual repair only.
#
# Validates before writing (unchanged from v1): >10 lines, canonical H1,
# order-parameters banner, "Last pass:" line present.
# Lock: mkdir lock via lib-lock.sh (exit 5 on timeout). Previous version kept
# at research/candidates.md.prev.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
target="$repo_root/research/candidates.md"
# shellcheck source=lib-lock.sh
. "$repo_root/scripts/lib-lock.sh"

expect=""
if [ "$#" -eq 2 ] && [ "$1" = "--expect-last-pass" ]; then
  expect="$2"
elif [ "$#" -ne 0 ]; then
  echo "research-write: usage: research-write.sh [--expect-last-pass 'Last pass: <value>'] < content" >&2
  exit 2
fi

content="$(cat)"

line_count="$(printf '%s\n' "$content" | wc -l | tr -d ' ')"
if [ "$line_count" -le 10 ]; then
  echo "research-write: refused — only $line_count lines (truncated heredoc?)" >&2
  exit 1
fi

first_line="$(printf '%s\n' "$content" | head -1)"
if [ "$first_line" != "# Research candidates" ]; then
  echo "research-write: refused — first line must be '# Research candidates'" >&2
  exit 1
fi

if ! printf '%s\n' "$content" | grep -q "never a source for order parameters"; then
  echo "research-write: refused — missing the order-parameters banner" >&2
  exit 1
fi

if ! printf '%s\n' "$content" | grep -q "^Last pass:"; then
  echo "research-write: refused — missing the 'Last pass:' timestamp line" >&2
  exit 1
fi

mkdir -p "$repo_root/research"
if ! acquire_lock "$target" 10; then
  echo "research-write: refused — could not acquire lock (another writer active?)" >&2
  exit 5
fi

if [ -n "$expect" ] && [ -f "$target" ]; then
  current="$(grep '^Last pass:' "$target" | head -1 || true)"
  if [ "$current" != "$expect" ]; then
    echo "research-write: refused — Last pass mismatch (CAS)." >&2
    echo "  expected: $expect" >&2
    echo "  current:  $current" >&2
    echo "  Another writer landed. Re-read candidates.md, merge, retry." >&2
    exit 3
  fi
fi

if [ -f "$target" ]; then
  cp "$target" "$target.prev"
fi
printf '%s\n' "$content" > "$target"
echo "research-write: wrote $line_count lines to research/candidates.md"
```

- [ ] **Step 3: Build the test scratch and run the failure cases first**

```bash
T=/private/tmp/claude-501/-Users-chris-Documents-Projects-trade-challenge/8b45bad6-1080-46b2-ad82-083aa8b6ed02/scratchpad/deeptest
rm -rf "$T" && mkdir -p "$T" && cp -R scripts "$T/" && mkdir -p "$T/research"
# Seed a fixture candidates.md (12 lines, valid):
{ echo '# Research candidates'; echo; echo 'This file is never a source for order parameters — banner.'; echo; echo 'Last pass: 2026-08-14 16:04 ET'; for i in 1 2 3 4 5 6 7; do echo "line $i"; done; } > "$T/research/candidates.md"
# CASE A — CAS mismatch must refuse with exit 3:
{ echo '# Research candidates'; echo 'never a source for order parameters'; echo 'Last pass: 2026-08-14 17:00 ET'; for i in $(seq 1 9); do echo "new $i"; done; } | "$T/scripts/research-write.sh" --expect-last-pass 'Last pass: 2026-08-14 09:00 ET'; echo "exit=$?"
# CASE B — lock held must refuse with exit 5:
mkdir "$T/research/candidates.md.lock.d"
{ echo '# Research candidates'; echo 'never a source for order parameters'; echo 'Last pass: 2026-08-14 17:00 ET'; for i in $(seq 1 9); do echo "new $i"; done; } | "$T/scripts/research-write.sh" --expect-last-pass 'Last pass: 2026-08-14 16:04 ET'; echo "exit=$?"
rmdir "$T/research/candidates.md.lock.d"
```

Expected: CASE A prints the CAS refusal and `exit=3`; CASE B waits ~10s, prints lock refusal, `exit=5`. File content unchanged after both (verify `grep '^Last pass:' "$T/research/candidates.md"` still says 16:04).

- [ ] **Step 4: Run the success case**

```bash
{ echo '# Research candidates'; echo 'never a source for order parameters'; echo 'Last pass: 2026-08-14 17:00 ET'; for i in $(seq 1 9); do echo "new $i"; done; } | "$T/scripts/research-write.sh" --expect-last-pass 'Last pass: 2026-08-14 16:04 ET'; echo "exit=$?"
grep '^Last pass:' "$T/research/candidates.md"; ls "$T/research/candidates.md.prev" && [ ! -d "$T/research/candidates.md.lock.d" ] && echo "lock released"
```

Expected: `exit=0`, file now stamped 17:00, `.prev` exists, lock dir gone. Also re-run with **no flag** (manual-repair path) and confirm it still writes (exit 0).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib-lock.sh scripts/research-write.sh
git commit -m "research-write: mkdir lock + compare-and-swap on Last pass (design rev2 §8.2)"
```

---

### Task 2: Idempotency guard in oi-append.sh

**Files:**
- Modify: `scripts/oi-append.sh`
- Test: scratch-copy run

**Interfaces:**
- Produces: `oi-append.sh DATE JSON` — NEW exit 4 = duplicate refused (symbol already has a row in `research/oi/DATE.jsonl`). Callers treat exit 4 as "already done, skip" — not an error to retry.
- Consumes: nothing new.

- [ ] **Step 1: Insert the duplicate check** — after the existing JSON validation (line 40) and before the append, add:

```bash
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
file="$repo_root/research/oi/$date.jsonl"
sym="$(printf '%s' "$json" | jq -r .symbol)"

if [ -f "$file" ] && jq -e -s --arg s "$sym" 'map(select(.symbol == $s)) | length > 0' "$file" >/dev/null 2>&1; then
  echo "oi-append: refused — $sym already snapshotted in oi/$date.jsonl today (idempotency guard, design rev2 §8.1). Not an error; skip this underlying." >&2
  exit 4
fi
```

(Delete the now-duplicated `repo_root=` line from the old append block; keep `mkdir -p` and the append itself unchanged.)

- [ ] **Step 2: Test both paths in the scratch copy**

```bash
cp scripts/oi-append.sh "$T/scripts/"
"$T/scripts/oi-append.sh" 2026-08-14 '{"symbol":"WMT","t":"16:20:00","spot":115.27}'; echo "first=$?"
"$T/scripts/oi-append.sh" 2026-08-14 '{"symbol":"WMT","t":"16:25:00","spot":115.30}'; echo "dup=$?"
"$T/scripts/oi-append.sh" 2026-08-14 '{"symbol":"USB","t":"16:20:00","spot":65.42}'; echo "other=$?"
wc -l "$T/research/oi/2026-08-14.jsonl"
```

Expected: `first=0`, `dup=4`, `other=0`, file has exactly **2** lines.

- [ ] **Step 3: Commit**

```bash
git add scripts/oi-append.sh
git commit -m "oi-append: refuse duplicate symbol+date rows (design rev2 §8.1 idempotency)"
```

---

### Task 3: research-append.sh — validated jsonl appends (screen | iv | tombstones)

**Files:**
- Create: `scripts/research-append.sh`
- Test: scratch-copy run

**Interfaces:**
- Produces: `research-append.sh TARGET DATE JSON` where TARGET ∈ `screen`→`research/screen/DATE.jsonl`, `iv`→`research/iv/DATE.jsonl`, `tombstones`→`research/tombstones.jsonl` (single file; DATE must equal the record's `.date`). Exit 0 ok, 1 validation refusal, 2 usage. Required fields — screen: `symbol`,`t`,`src`; iv: `symbol`,`t`,`atm_iv`; tombstones: `symbol`,`date`,`gate`,`reason`,`ref_price` (add `hypo_qty`,`hypo_stop` when a hypothetical position is scored — spec §5).
- Consumes: nothing.

- [ ] **Step 1: Write the script**

```bash
#!/bin/bash
# Validated jsonl append for the deep-research file tree
# (design rev2 §8.3 whitelist). Targets:
#   screen     -> research/screen/DATE.jsonl   (required: symbol, t, src)
#   iv         -> research/iv/DATE.jsonl       (required: symbol, t, atm_iv)
#   tombstones -> research/tombstones.jsonl    (required: symbol, date, gate,
#                 reason, ref_price; DATE arg must equal .date. Optional
#                 hypo_qty/hypo_stop for scorecard forward-marking, spec §5.)
# Usage: scripts/research-append.sh TARGET DATE 'JSON_OBJECT'
# DATE: YYYY-MM-DD Eastern (from get_datetime, never the machine clock).
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "research-append: expected TARGET DATE JSON, got $# args" >&2; exit 2
fi
target_kind="$1"; date="$2"; json="$3"

if ! [[ "$date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "research-append: DATE must be YYYY-MM-DD, got '$date'" >&2; exit 2
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
case "$target_kind" in
  screen)     file="$repo_root/research/screen/$date.jsonl";  req='has("symbol") and has("t") and has("src")' ;;
  iv)         file="$repo_root/research/iv/$date.jsonl";      req='has("symbol") and has("t") and has("atm_iv")' ;;
  tombstones) file="$repo_root/research/tombstones.jsonl";    req='has("symbol") and has("date") and has("gate") and has("reason") and has("ref_price")' ;;
  *) echo "research-append: TARGET must be screen|iv|tombstones, got '$target_kind'" >&2; exit 2 ;;
esac

if ! printf '%s' "$json" | jq -e "type == \"object\" and ($req)" >/dev/null 2>&1; then
  echo "research-append: JSON failed validation for target '$target_kind' (required: $req)" >&2
  exit 1
fi

if [ "$target_kind" = "tombstones" ]; then
  jdate="$(printf '%s' "$json" | jq -r .date)"
  if [ "$jdate" != "$date" ]; then
    echo "research-append: tombstone .date ($jdate) must equal DATE arg ($date)" >&2; exit 1
  fi
fi

mkdir -p "$(dirname "$file")"
printf '%s\n' "$(printf '%s' "$json" | jq -c .)" >> "$file"
echo "research-append: appended $(printf '%s' "$json" | jq -r .symbol) to ${file#"$repo_root"/}"
```

- [ ] **Step 2: Test — refusals then successes**

```bash
chmod +x scripts/research-append.sh && cp scripts/research-append.sh "$T/scripts/"
"$T/scripts/research-append.sh" screen 2026-08-15 '{"symbol":"GAP","t":"16:30:00"}'; echo "missing-src=$?"          # expect 1
"$T/scripts/research-append.sh" bogus 2026-08-15 '{"symbol":"GAP"}'; echo "bad-target=$?"                            # expect 2
"$T/scripts/research-append.sh" tombstones 2026-08-15 '{"symbol":"HD","date":"2026-08-14","gate":"size","reason":"x","ref_price":338.86}'; echo "date-mismatch=$?"  # expect 1
"$T/scripts/research-append.sh" screen 2026-08-15 '{"symbol":"GAP","t":"16:30:00","src":"universe-screen","rank":3}'; echo "ok=$?"   # expect 0
"$T/scripts/research-append.sh" iv 2026-08-15 '{"symbol":"WMT","t":"16:30:00","atm_iv":29.0}'; echo "ok=$?"                          # expect 0
"$T/scripts/research-append.sh" tombstones 2026-08-14 '{"symbol":"HD","date":"2026-08-14","gate":"size-line","reason":"unsizeable at any qty >= 1","ref_price":338.86}'; echo "ok=$?"  # expect 0
```

Expected exit codes as annotated; three files exist under `$T/research/`.

- [ ] **Step 3: Commit**

```bash
git add scripts/research-append.sh
git commit -m "research-append: validated jsonl appends for screen/iv/tombstones (design rev2 §8.3)"
```

---

### Task 4: research-replace.sh — locked full replacement (roster | preopen | scorecard | universe)

**Files:**
- Create: `scripts/research-replace.sh`
- Test: scratch-copy run

**Interfaces:**
- Consumes: `acquire_lock`/`release_lock` from `scripts/lib-lock.sh` (Task 1).
- Produces: `research-replace.sh TARGET [DATE] < content` — TARGET ∈ `roster`→`research/options-roster.md`, `preopen DATE`→`research/preopen/DATE.md`, `scorecard`→`research/scorecard.md`, `universe`→`research/universe.md`. Exit 0 ok, 1 validation refusal, 2 usage, 5 lock timeout. `.prev` kept beside each target.

- [ ] **Step 1: Write the script**

```bash
#!/bin/bash
# Locked, validated full-replacement writer for the deep-research file tree
# (design rev2 §8.2/§8.3). candidates.md has its own script (research-write.sh);
# this one covers the other replace-style targets:
#   roster          -> research/options-roster.md
#   preopen DATE    -> research/preopen/DATE.md
#   scorecard       -> research/scorecard.md
#   universe        -> research/universe.md
# Usage: scripts/research-replace.sh TARGET [DATE] <<'EOF' ... EOF
# Per-target validation enforces each file's required first line and the
# banner lines the spec demands verbatim (rev2 §5, §6.1, §7.1).
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib-lock.sh
. "$repo_root/scripts/lib-lock.sh"

if [ "$#" -lt 1 ]; then
  echo "research-replace: usage: research-replace.sh TARGET [DATE] < content" >&2; exit 2
fi
target_kind="$1"

case "$target_kind" in
  roster)
    [ "$#" -eq 1 ] || { echo "research-replace: roster takes no DATE" >&2; exit 2; }
    file="$repo_root/research/options-roster.md"
    h1='# Options-viable roster'
    banners=("never a source for order parameters" "TTL") ;;
  preopen)
    [ "$#" -eq 2 ] || { echo "research-replace: preopen requires DATE" >&2; exit 2; }
    [[ "$2" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "research-replace: bad DATE '$2'" >&2; exit 2; }
    file="$repo_root/research/preopen/$2.md"
    h1="# Pre-open brief — $2"
    banners=("Pre-market data informs, it never qualifies") ;;
  scorecard)
    [ "$#" -eq 1 ] || { echo "research-replace: scorecard takes no DATE" >&2; exit 2; }
    file="$repo_root/research/scorecard.md"
    h1='# Research scorecard'
    banners=("never loosens a gate in-flight" "explicit conversation with Chris") ;;
  universe)
    [ "$#" -eq 1 ] || { echo "research-replace: universe takes no DATE" >&2; exit 2; }
    file="$repo_root/research/universe.md"
    h1='# Fallback universe'
    banners=("never a source for order parameters") ;;
  *) echo "research-replace: TARGET must be roster|preopen|scorecard|universe, got '$target_kind'" >&2; exit 2 ;;
esac

content="$(cat)"
line_count="$(printf '%s\n' "$content" | wc -l | tr -d ' ')"
if [ "$line_count" -le 5 ]; then
  echo "research-replace: refused — only $line_count lines (truncated heredoc?)" >&2; exit 1
fi
first_line="$(printf '%s\n' "$content" | head -1)"
if [ "$first_line" != "$h1" ]; then
  echo "research-replace: refused — first line must be '$h1'" >&2; exit 1
fi
for b in "${banners[@]}"; do
  if ! printf '%s\n' "$content" | grep -qF "$b"; then
    echo "research-replace: refused — required banner text missing: '$b'" >&2; exit 1
  fi
done

mkdir -p "$(dirname "$file")"
if ! acquire_lock "$file" 10; then
  echo "research-replace: refused — could not acquire lock on $file" >&2; exit 5
fi
if [ -f "$file" ]; then cp "$file" "$file.prev"; fi
printf '%s\n' "$content" > "$file"
echo "research-replace: wrote $line_count lines to ${file#"$repo_root"/}"
```

- [ ] **Step 2: Test — one refusal and one success per representative target**

```bash
chmod +x scripts/research-replace.sh && cp scripts/research-replace.sh "$T/scripts/"
printf '%s\n' '# Wrong H1' a b c d e f | "$T/scripts/research-replace.sh" roster; echo "badh1=$?"           # expect 1
printf '%s\n' '# Options-viable roster' 'never a source for order parameters' 'TTL: 5 sessions' d e f g | "$T/scripts/research-replace.sh" roster; echo "roster=$?"   # expect 0
printf '%s\n' '# Pre-open brief — 2026-08-17' 'Pre-market data informs, it never qualifies.' c d e f g | "$T/scripts/research-replace.sh" preopen 2026-08-17; echo "preopen=$?"  # expect 0
printf '%s\n' '# Research scorecard' 'never loosens a gate in-flight' 'explicit conversation with Chris' d e f g | "$T/scripts/research-replace.sh" scorecard; echo "scorecard=$?"  # expect 0
ls "$T/research/options-roster.md" "$T/research/preopen/2026-08-17.md" "$T/research/scorecard.md"
```

Expected: exit codes 1/0/0/0, three files created, no `.lock.d` left behind.

- [ ] **Step 3: Commit**

```bash
git add scripts/research-replace.sh
git commit -m "research-replace: locked validated writer for roster/preopen/scorecard/universe (design rev2 §8.2-8.3)"
```

---

### Task 5: Tombstone migration out of candidates.md

**Files:**
- Create: `research/tombstones.jsonl` (via Task 3's script — 8 records from the current candidates.md tombstone table)
- Modify: `research/candidates.md` (via research-write.sh — replace the tombstone table with a one-line index)
- Modify: `docs/superpowers/specs/2026-08-14-deep-research-design.md` §8.7 (record `.jsonl` instead of `.md` — machine-readable gate + ref_price is what the scorecard consumes; note the change inline)

**Interfaces:**
- Consumes: `research-append.sh tombstones` (Task 3), `research-write.sh --expect-last-pass` (Task 1).
- Produces: `research/tombstones.jsonl` — append-only archive of record; candidates.md carries only `## Tombstones — see research/tombstones.jsonl (append-only archive)` plus a symbol list one-liner.

- [ ] **Step 1: Append the 8 existing tombstones** (MNDY, HD, HLIT, BMY, SMCI, WDAY, AXON, MAERSK) to the real `research/tombstones.jsonl`, one `research-append.sh tombstones 2026-08-14 '{...}'` call each. Field mapping from the current table — `symbol`, `date`:"2026-08-14", `gate` (MNDY:"gap-and-hold+atr-ceiling", HD:"size-line", HLIT:"gap-and-hold+atr-ceiling", BMY:"corporate-overhang", SMCI:"atr-ceiling", WDAY:"earnings-gate", AXON:"size-line", MAERSK:"exchange-floor"), `reason` (compress the table cell to ≤2 sentences, keep the revival condition), `ref_price` (MNDY 88.50, HD 338.86, HLIT 13.95, BMY 63.72, SMCI 39.69, WDAY 197.65, AXON 611.67, MAERSK 0 — no US quote). Verify: `wc -l research/tombstones.jsonl` = 8 and `jq -r .symbol research/tombstones.jsonl` lists all 8.

- [ ] **Step 2: Rewrite candidates.md** — read the current file, note its exact `Last pass:` line, and re-emit it unchanged EXCEPT the `## Tombstones` section, which becomes:

```markdown
## Tombstones — archive moved 2026-08-14

The archive of record is **`research/tombstones.jsonl`** (append-only, via
`scripts/research-append.sh tombstones`). Revival conditions live there.
Tombstoned so far: MNDY, HD, HLIT, BMY, SMCI, WDAY, AXON, MAERSK — check
before researching any name.
```

Write via `scripts/research-write.sh --expect-last-pass 'Last pass: <exact line read>'`. Expected: exit 0. Then `grep -c 'Disqualifier' research/candidates.md` returns 0.

- [ ] **Step 3: Amend spec §8.7** — change `research/tombstones.md` to `research/tombstones.jsonl`, adding: "(.jsonl, not .md as rev 2 first wrote: the scorecard consumes gate and ref_price fields, so the archive is machine-readable; the human index stays in candidates.md)". Also update the §8.3 whitelist line to say `research/tombstones.jsonl` (via research-append.sh).

- [ ] **Step 4: Commit**

```bash
git add research/tombstones.jsonl research/candidates.md docs/superpowers/specs/2026-08-14-deep-research-design.md
git commit -m "tombstones: move archive to append-only research/tombstones.jsonl (design rev2 §8.7, finding 12)"
```

---

### Task 6: Scorecard seed file

**Files:**
- Create: `research/scorecard.md` (via `research-replace.sh scorecard`)

**Interfaces:**
- Consumes: `research-replace.sh` (Task 4).
- Produces: the scorecard's standing structure; the deep-research command file (Task 9) references its section names verbatim (`## Open cohorts`, `## Closed cohorts`, `## Weekly synthesis`).

- [ ] **Step 1: Write the seed via the script**

```bash
scripts/research-replace.sh scorecard <<'EOF'
# Research scorecard

**Rules of this file (verbatim per design rev2 §5 — do not soften):**
1. This scorecard informs rule changes in calm conditions only and never
   loosens a gate in-flight. A gate that "cost" money over a small sample is
   the §0 pressure quantified — not evidence.
2. Changes to *(strategy rule)* gates, although they do not require §9,
   require an **explicit conversation with Chris** recorded in a commit.
3. Marks come from daily OHLC. A hypothetical stop is HIT if session low ≤
   stop, filled at min(stop, open). Stop-adjusted and close-only numbers are
   reported side by side. SPY over the same window is the control.
4. Every aggregate prints its n. Below n = 10: "sample too small for
   inference."

Maintained only by the deep-research post-close run via
scripts/research-replace.sh. Cohort horizon: 5 sessions.

## Open cohorts

(none yet — cohorts open when a tombstone, gate-kill, declined ping, or
screener shortlist gets its hypothetical position recorded)

## Closed cohorts

(none yet)

## Weekly synthesis

(first synthesis due Friday 2026-08-21 post-close)
EOF
```

Expected: exit 0. (The required banners are in rules 1–2; validation passes.)

- [ ] **Step 2: Commit**

```bash
git add research/scorecard.md
git commit -m "scorecard: seed file with integrity rules (design rev2 §5)"
```

---

### Task 7: API spike (FMP → Finnhub) — verdict document

**Files:**
- Create: `docs/superpowers/research/2026-08-14-screener-api-spike.md`

**Interfaces:**
- Produces: a written verdict (`FMP` / `Finnhub` / `no-API`) that Task 9's command file cites; env var name `FMP_API_KEY` (or `FINNHUB_API_KEY`) if a provider passes.
- Consumes: nothing. **Spike code is throwaway; only the document is kept.**

- [ ] **Step 1: Ask Chris for a key** — this step is **blocked on Chris**: "Register a free FMP account (financialmodelingprep.com) and export `FMP_API_KEY` in the shell profile, or say 'no-API' to skip." If Chris opts out, record verdict `no-API` in the doc and skip to Step 4.

- [ ] **Step 2: Probe the two endpoints the design needs** (curl, key from env, never echo the key):

```bash
curl -s "https://financialmodelingprep.com/api/v3/stock-screener?priceMoreThan=5&priceLowerThan=1015&volumeMoreThan=1000000&exchange=NYSE,NASDAQ&limit=50&apikey=$FMP_API_KEY" | jq 'if type=="array" then {n: length, sample: .[0]} else . end'
curl -s "https://financialmodelingprep.com/api/v3/earning_calendar?from=2026-08-10&to=2026-08-14&apikey=$FMP_API_KEY" | jq 'if type=="array" then {n: length, sample: .[0]} else . end'
```

A JSON error object (e.g. "Exclusive Endpoint" / "upgrade") on either = that endpoint fails. If FMP fails, probe Finnhub equivalents (`/stock/symbol`, `/calendar/earnings`) the same way; Finnhub has no screener, so it can at best support the drift screen.

- [ ] **Step 3: Rate-limit check** — read the response headers (`curl -sI` or `-D -`) for limit headers; note the daily cap in the doc (spec §8.5: the ledger records provider headers when present).

- [ ] **Step 4: Write the verdict doc** — provider chosen (or `no-API`), which endpoints passed, observed limits, exact request shapes that worked, and the sentence: "`get_movers` retirement is effective only from this verdict forward (design rev2 §3.3); if verdict is no-API, the Task 8 universe file is the screener until revisited."

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/research/2026-08-14-screener-api-spike.md
git commit -m "screener API spike verdict (design rev2 §10.1)"
```

---

### Task 8: No-API fallback universe file

**Files:**
- Create: `research/universe.md` (via `research-replace.sh universe`)

**Interfaces:**
- Consumes: `research-replace.sh` (Task 4); WebFetch (constituent lists); batched `get_quotes` (~10–12 calls, one-time, main session — NOT inside any cron budget).
- Produces: a static, weekly-refreshed universe list the command file's no-API branch sweeps.

- [ ] **Step 1: Assemble the raw list** — fetch S&P 500 and S&P 400 constituent tables (Wikipedia "List of S&P 500 companies" / "List of S&P 400 companies"), extract tickers + GICS sector into a scratch file. Expected: ~900 symbols.

- [ ] **Step 2: Filter by band via batched quotes** — `get_quotes` in chunks of ~50; keep symbols with last price $5–$1,015 and 10-day avg volume ≥ 1M (quote payload carries `totalVolume`; a single day ≥ 1.5M is an acceptable proxy — note which was used). Flag the $20–60 band and the ≤$105 options-band separately.

- [ ] **Step 3: Write `research/universe.md`** via the script. Required shape (validation needs the H1 and banner):

```markdown
# Fallback universe

This file is never a source for order parameters — every candidate
re-verifies live under §4.9/§4.10. No-API discovery branch (design rev2
§3.3): when the screener API is unavailable, the deep run sweeps THIS list
with batched get_quotes and applies the §4 tilts to the survivors.

Assembled: 2026-08-14 from S&P 500 + S&P 400 constituents, filtered to
price $5–$1,015 and ADV ≥ 1M. Refresh weekly (weekend), method identical.

## $20–60 band (§5-preferred)
| Symbol | Sector | Price @ assembly |
...

## Options band (≤ ~$105)
...

## Rest of sizeable universe
...
```

- [ ] **Step 4: Commit**

```bash
git add research/universe.md
git commit -m "fallback universe: static S&P 500+400 band-filtered list (design rev2 §3.3)"
```

---

### Task 9: Command file — .claude/commands/deep-research.md

**Files:**
- Create: `.claude/commands/deep-research.md`

**Interfaces:**
- Consumes: every script above by exact name/flag; spec sections 3–8 (transcribe constraints, do not summarize them away); Task 7's verdict doc path.
- Produces: `/deep-research preopen` and `/deep-research postclose` modes that Task 10's agent executes and Task 12's crons invoke; return-line contract `DEEP <mode> <ET time> | screened n | roster n/M | cohorts n | skipped: <features or ->`.

- [ ] **Step 1: Write the command file.** It must contain, at minimum, these sections — the text below is the required content skeleton with every constraint the spec mandates; expand prose but never weaken a constraint:

```markdown
---
description: One deep-research run (preopen | postclose). Dispatches the deep-research agent; owns the POST window (postclose mode).
argument-hint: "preopen | postclose"
---

# /deep-research — the deep pass (design: 2026-08-14-deep-research-design.md rev 2)

## §Dispatch
Parent checks §A gates, then dispatches `.claude/agents/deep-research.md`
in the background with cached context: date + ET time, held symbols +
sectors, comp capital SOURCE = the "State recorded — current" block of the
latest status/*.md (echo the figure and its date), active calendar guards,
mode ($ARGUMENTS: preopen | postclose). Fallback + failure handling as
research.md §Dispatch (quiet after 2 consecutive failures).

## §A — Preconditions
1. Mode required; unknown mode = no-op.
2. Halt / restriction / cash call (latest tick or status): no runs.
3. Unacknowledged ALERT.md: run is file-only (postclose parent §E suppressed;
   preopen is file-only always).
4. §8 endgame: from 9/8, no runs.
5. postclose only: if research/oi/DATE.jsonl already has today's §B-oi rows
   AND screen/DATE.jsonl exists, today's run already happened — no-op.

## §P — preopen mode (8:15 ET; FILE-ONLY, design rev2 §7.1)
Budget ceiling: ~6 Schwab + ~8 API/web.
- NO pings, NO §E, NO HOT promotions, NO candidates.md writes. Writes
  exactly one file: research/preopen/DATE.md via research-replace.sh.
- Content: (1) earnings digests for calendar-watch names that printed
  overnight/pre-market — actual vs consensus, guidance, pre-market
  price/volume; (2) overnight news on held names + HOT candidates;
  (3) refreshed sleeve-live event calendar.
- The file header carries verbatim: "Pre-market data informs, it never
  qualifies. No §5 gate is satisfiable from pre-market data, and this brief
  is not a source of entry decisions — it is preparation for evaluating
  them live."

## §D — postclose mode (16:20 ET; owns the POST window, design rev2 §8.1)
Budget ceiling: ~15 Schwab + ~15 API/web. HARD PRIORITY ORDER — spend
top-down, log what the budget never reached:
1. §B-oi snapshot (per research.md §B-oi; oi-append.sh exit 4 = already
   done, skip that underlying silently)
2. Scorecard forward-marks: for each open cohort name, one
   get_advanced_price_history (daily bars since cohort open); stop HIT if
   low ≤ stop, fill at min(stop, open); SPY once per run as control;
   rewrite scorecard.md via research-replace.sh. Fridays: weekly synthesis.
3. Universe + drift screens (API per spike verdict, else universe.md sweep
   via batched get_quotes): append ranked rows to screen/DATE.jsonl via
   research-append.sh; top 3–5 only enter candidates.md at WATCH tagged
   `source: screener` via research-write.sh --expect-last-pass.
4. Roster chain-checks: a few names/day, TTL-expired first (verdict older
   than 5 sessions = absent); rewrite options-roster.md via
   research-replace.sh roster.
5. ETF track refresh (§4 tilts + 60-day correlation vs held book;
   leveraged/inverse NEVER surfaced).
6. Deeper vetting — only if a WATCH→HOT promotion is pending: short
   interest, revisions, sector RS (from ETF data), overhang news scan.
7. IV series append (research-append.sh iv) — §B-oi universe only, ≤6.
Every run ends by logging one events-corpus line:
  {"t":"HH:MM:SS","event":"deep_research","mode":"postclose",
   "skipped":"<features or ->","api_calls":N,"schwab_calls":N}
plus provider rate-limit headers when present (ledger, design rev2 §8.5).

## §W — Write whitelist (restates §G — design rev2 §8.3; the agent has
Write/Edit withheld and these scripts are the ONLY write paths):
candidates.md (research-write.sh --expect-last-pass; carry the Last pass:
line forward UNCHANGED — the stamp belongs to the intraday cadence gate),
oi/DATE.jsonl (oi-append.sh), screen/DATE.jsonl + iv/DATE.jsonl +
tombstones.jsonl (research-append.sh), options-roster.md +
preopen/DATE.md + scorecard.md + universe.md (research-replace.sh).
Nothing else, ever. All other §G lines carry over unchanged: single
promotion path, WATCH-before-HOT (never HOT in the pass a name was first
found; HOT requires an RTH-timestamped quote), never a source of order
parameters, never interrupt the monitoring loop, quiet on failure — log
to the events corpus and stop; never ALERT.md, never a ping on failure.
CAS refusal (exit 3) from research-write.sh: re-read, merge onto the
fresh copy, retry ONCE; second refusal = log and skip candidates.md this
run.
```

- [ ] **Step 2: Consistency check against the spec** — walk rev 2 sections 3–8 one by one and confirm each constraint appears in the file (checklist in the task, no code). In particular: comp-capital source (§3.1), no-API branch reference (§3.3), scorecard mark rules (§5), roster cap ~20–30 + TTL (§6.1), IV series ≤6 + "series not rank" (§6.2), preopen hard constraints (§7.1), priority order + skipped line (§8.4), deadman not here (it lives in the playbook, Task 13).

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/deep-research.md
git commit -m "deep-research command: preopen/postclose modes, POST ownership, priority budget (design rev2 §3-8)"
```

---

### Task 10: Agent file — .claude/agents/deep-research.md

**Files:**
- Create: `.claude/agents/deep-research.md`

**Interfaces:**
- Consumes: the command file (Task 9) — the agent executes its §P/§D.
- Produces: the dispatchable `deep-research` agent type.

- [ ] **Step 1: Write the agent file**

```markdown
---
name: deep-research
description: Read-only deep-research agent — executes one /deep-research run (preopen or postclose per .claude/commands/deep-research.md). No order tools, no account tools, no Write/Edit by construction; writes only via the §W whitelist scripts. Pinging and all §E decisions belong to the parent session.
tools: Read, Glob, Grep, Bash, ToolSearch, WebSearch, WebFetch, mcp__schwab__get_datetime, mcp__schwab__get_market_hours, mcp__schwab__get_quotes, mcp__schwab__get_instruments, mcp__schwab__get_option_chain, mcp__schwab__get_option_expiration_chain, mcp__schwab__get_advanced_price_history, mcp__schwab__get_advanced_option_chain
model: opus
---

You are the deep-research agent for the trading competition in
/Users/chris/Documents/Projects/trade-challenge. One invocation = one run,
in the mode the dispatch prompt names (preopen | postclose). You research;
you never trade, never ping, never decide entries.

Procedure — no improvisation:
1. Read `.claude/commands/deep-research.md` and execute the section for
   your mode (§P or §D) exactly as written, inside its budget ceiling and
   in its priority order. Load Schwab schemas via ToolSearch.
2. Qualification rules live in the playbook (§4/§5/§6) and the manual
   (CLAUDE.md §1.4, §2, §3.2, §3.7). Read them fresh each run; never
   qualify from memory. Zero qualified anything is a legitimate outcome.
3. The dispatch prompt supplies cached context (date/ET time, held symbols
   + sectors, comp-capital figure + its status-file date, guards, mode).
   Trust it; you have no account tools by design. Sizing math uses the
   supplied comp-capital figure; every reference price you record carries
   its quote timestamp.
4. Your write paths are the §W whitelist in the command file — script-
   mediated, nothing else, anywhere. candidates.md writes use
   --expect-last-pass and carry the Last pass: line forward unchanged.
5. preopen mode: file-only. One output file. No candidates.md write, no
   HOT anything, regardless of what you find — flag it in the brief for
   the RTH session to evaluate live.

Return value (machine-consumed, not prose):
- Line 1: `DEEP <mode> <ET time> | screened n | roster n/M | cohorts n | skipped: <features or ->`
- Line 2 (postclose only, only if a name newly reached HOT with a full
  written checklist and an RTH-timestamped quote this run):
  `HOT-FRESH: SYMBOL sleeve=<core|catalyst|option> ref=<price>@<ts> — <one-line thesis>`
- Line 3 (only on failure): `FAIL: <what could not be read or written>`
Nothing else.
```

- [ ] **Step 2: Verify the tool surface** — confirm the frontmatter lists no `get_account`/`get_accounts`/`get_orders`/order tools and no Write/Edit/NotebookEdit. (This IS the harness enforcement — spec §8.3.)

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/deep-research.md
git commit -m "deep-research agent: read-only tool surface, mode dispatch (design rev2 §8.3)"
```

---

### Task 11: Supervised dry run (before any cron exists)

**Files:** none created (run output lands in the research/ tree via the machinery under test).

**Interfaces:**
- Consumes: everything above.
- Produces: evidence the pipeline works end-to-end; fixes for whatever it surfaces.

- [ ] **Step 1: Dispatch `/deep-research postclose` manually** from the main session (market is closed; today's POST already ran under the old path, so this doubles as the idempotency test). Expected observations, each verified by reading the artifacts: (a) §B-oi step hits exit-4 duplicates for WMT/USB/CSX and **skips them without error**; (b) `research/screen/2026-08-14.jsonl` gets rows (API or universe-sweep branch per the spike verdict); (c) at most 3–5 screener names enter candidates.md as WATCH, `Last pass:` line **unchanged**; (d) the events corpus gains one `deep_research` line with a truthful `skipped:` list; (e) return line matches the `DEEP ...` contract.

- [ ] **Step 2: Dispatch `/deep-research preopen` manually** (timing is wrong — that's fine, it's a mechanics test). Expected: exactly one file created (`research/preopen/DATE.md`) with the verbatim informs-never-qualifies header; **no** candidates.md diff (`git diff --stat research/candidates.md` empty); no ping.

- [ ] **Step 3: Fix-and-rerun loop** — anything that deviates gets fixed and the dry run repeated until both modes pass clean. Commit fixes individually with descriptive messages.

- [ ] **Step 4: Commit the dry-run artifacts**

```bash
git add research/ status/data/
git commit -m "deep-research dry run: both modes verified against design rev2 (pre-cron)"
```

---

### Task 12: research.md §A.4 amendment + cron installation (ONE commit — spec §10.3)

**Files:**
- Modify: `.claude/commands/research.md` (§A.4, and §B-oi header note)
- Create: two harness cron entries (not files — via CronCreate)

**Interfaces:**
- Consumes: dry-run-verified `/deep-research` (Task 11).
- Produces: the POST window owned by the 16:20 run; intraday loop research-free after 16:00 ET.

- [ ] **Step 1: Amend research.md §A.4** — replace

```
4. **POST (after 16:00 ET):** run the one deeper post-close pass — §5
   catalyst setups form after the close — then no more passes today.
```

with

```
4. **After 16:00 ET: no research passes from the tick-chained loop.** The
   POST pass — including §B-oi — is owned exclusively by the 16:20 ET
   deep-research run (/deep-research postclose; design rev2 §8.1). A
   chained invocation after 16:00 simply stops here.
```

Also add to the §B-oi heading line: "(now executed only inside /deep-research postclose)". And in §D, change the write instruction to name the CAS flag: "via `scripts/research-write.sh --expect-last-pass '<the Last pass: line read at compose time>'` (on exit-3 refusal: re-read, merge, retry once)". Update `.claude/agents/research-scout.md` item 4 to mention the same flag.

- [ ] **Step 2: Install the crons** — load CronCreate via ToolSearch, then create two weekday entries (times are **PT local**; ET−3): `15 5 * * 1-5` → prompt `/deep-research preopen`; `20 13 * * 1-5` → prompt `/deep-research postclose`. Verify both with CronList, and confirm the existing tick/research chain cron (`5b5caf1b`) is untouched.

- [ ] **Step 3: Single atomic commit**

```bash
git add .claude/commands/research.md .claude/agents/research-scout.md
git commit -m "POST window moves to /deep-research postclose cron; §A.4 amended + crons installed atomically (design rev2 §8.1/§10.3); scout adopts CAS flag"
```

(The cron entries live in harness state, not git; the commit message records their schedule so the repo tells the whole story.)

---

### Task 13: Playbook — deadman line in the session protocol

**Files:**
- Modify: `docs/superpowers/specs/2026-08-13-competition-strategy-design.md` §9 (Session protocol, ~line 289)

- [ ] **Step 1: Add to the session-open checklist** (after the §4.5 reconciliation item):

```markdown
- **Deep-research deadman (design rev2 §8.5):** check that yesterday's
  `research/preopen/` brief and `research/screen/` jsonl exist (mtime).
  If either is missing, say so in the session-open summary as a status
  line — not ALERT.md. A dead research loop gets noticed here, not weeks
  later. (Quiet, not loud — but never invisible.)
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-13-competition-strategy-design.md
git commit -m "playbook session protocol: deep-research deadman check (design rev2 §8.5)"
```

---

### Task 14: Playbook — post-amendment dollar sweep

**Files:**
- Modify: `docs/superpowers/specs/2026-08-13-competition-strategy-design.md` (lines 57, 75–78, 88, 141, 170, 179, 196 — grep `\$135|\$180|\$270|\$315|\$450|\$792|\$720` to catch all)

- [ ] **Step 1: Update every stale figure** to the $2,899.38 base, keeping the old number in parens for history, e.g. line 75–78 table → Core 50% ≈ $1,450 (was $450); Catalyst 30% ≈ $870 (was $270); Options $580 open / $435 per position (was $180/$135); Leveraged ETF 20% ≈ $580 (was $180). Line 88 → "(At HWM $2,900: $2,552/$2,320.)" Line 179 → "the $435 cap's arithmetic: effectively sub-$105 underlyings at calm IV". Add one line at the capital-architecture section top: "Amended figures per CLAUDE.md v3 capital amendment 2026-08-14 (`9619eaf`); percentages unchanged."

- [ ] **Step 2: Verify no stale figure remains**: the grep from the task header returns only lines that are explicit "(was $X)" history notes.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-13-competition-strategy-design.md
git commit -m "playbook: dollar figures updated to $2,899.38 comp capital (percentages unchanged)"
```

---

### Task 15: Close-out — status note and memory

**Files:**
- Modify: `status/2026-08-14.md` (or the current session's status file if executed later — append a "deep-research system live" block: what was built, cron schedule, first expected runs)
- Create: memory file `deep-research-system.md` in the memory directory (pointer: crons exist at 5:15/13:20 PT weekdays; deadman check at session open; scorecard rules require Chris conversation for strategy-gate changes) + MEMORY.md index line

- [ ] **Step 1: Write both, commit the status file**

```bash
git add status/
git commit -m "status: deep-research system live (design rev2 implemented)"
```

---

## Self-Review (performed at plan time)

**Spec coverage:** §3 screener → Tasks 7/8/9; §3.1 comp-capital source → Task 9 §Dispatch; §3.3 fallback → Tasks 7/8; §4 ETF → Task 9 §D.5; §5 scorecard → Tasks 6/9 §D.2; §6 roster/IV/calendar/vol → Task 9 §D.4/§D.7/§P; §7.1 preopen → Tasks 9 §P/10/11; §7.2 vetting → Task 9 §D.6; §8.1 → Tasks 2/12; §8.2 → Tasks 1/4; §8.3 → Tasks 3/4/9 §W/10; §8.4 → Task 9 §D; §8.5 → Tasks 9 (ledger line)/13; §8.6 → Task 12; §8.7 → Task 5; §10.2 → Task 14. No uncovered section.

**Known deviations from spec, recorded:** tombstones land as `.jsonl` not `.md` (Task 5 amends the spec inline with rationale).

**Type consistency:** script names/flags checked across Tasks 1–12 (`--expect-last-pass` exact flag; exit codes 1/2/3/4/5 consistent; target keywords `screen|iv|tombstones` and `roster|preopen|scorecard|universe` match between scripts and command file; return-line contracts identical in Tasks 9 and 10).
