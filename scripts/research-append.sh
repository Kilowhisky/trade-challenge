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
