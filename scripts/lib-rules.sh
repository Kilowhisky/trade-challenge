#!/usr/bin/env bash
# lib-rules.sh — load rules.yml into shell variables. Source, don't execute.
#
#   . "$(dirname "$0")/lib-rules.sh"
#   load_rules                      # or: load_rules /path/to/rules.yml
#   echo "$RULE_manual_single_position_pct"   # -> 35
#
# Every key in rules.yml becomes RULE_<section>_<key>. Nothing else is
# exported. No dependency beyond awk, deliberately: this is loaded by the
# pre-order gate, which must not acquire a runtime that could be missing at
# the moment an order needs checking.
#
# Fails loudly. A missing or malformed rules.yml must never silently degrade
# into "no caps applied" — see rule_get below.

load_rules() {
  local f="${1:-}"
  if [ -z "$f" ]; then
    local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    f="$here/../rules.yml"
  fi
  [ -r "$f" ] || { echo "lib-rules: cannot read rules file: $f" >&2; return 1; }

  local parsed
  parsed="$(awk '
    /^[[:space:]]*#/ { next }                       # comment
    /^[[:space:]]*$/ { next }                       # blank
    /^[a-zA-Z_][a-zA-Z0-9_]*:[[:space:]]*$/ {       # section header, indent 0
      section = $0; sub(/:.*$/, "", section); next
    }
    /^[[:space:]]+[a-zA-Z_][a-zA-Z0-9_]*:/ {        # key: value, indented
      line = $0
      sub(/^[[:space:]]+/, "", line)
      key = line; sub(/:.*$/, "", key)
      val = line; sub(/^[^:]*:[[:space:]]*/, "", val)
      sub(/[[:space:]]*#.*$/, "", val)              # strip trailing comment
      sub(/[[:space:]]+$/, "", val)
      if (section == "") { print "ERR:key outside section: " key; exit 1 }
      if (val == "")     { print "ERR:empty value for: " section "." key; exit 1 }
      if (val ~ /[^A-Za-z0-9._-]/) { print "ERR:unsafe value for " section "." key ": " val; exit 1 }
      printf "RULE_%s_%s=%s\n", section, key, val
      next
    }
    { print "ERR:unparseable line: " $0; exit 1 }
  ' "$f")" || { echo "lib-rules: awk failed on $f" >&2; return 1; }

  case "$parsed" in
    *ERR:*) echo "lib-rules: ${parsed#*ERR:}" >&2; return 1 ;;
  esac

  eval "$parsed"
  RULES_FILE="$f"
  RULES_LOADED=1
}

# rule_get NAME — echo a loaded rule, or fail hard if it is absent.
# Callers must use this rather than "${RULE_x:-default}": a defaulted cap is
# a cap that silently stops binding, which is the failure this file exists to
# prevent.
rule_get() {
  local name="RULE_$1" val
  [ "${RULES_LOADED:-0}" = "1" ] || { echo "rule_get: load_rules not called" >&2; return 1; }
  val="${!name-}"
  [ -n "$val" ] || { echo "rule_get: no such rule: $1 (in ${RULES_FILE:-?})" >&2; return 1; }
  printf '%s' "$val"
}
