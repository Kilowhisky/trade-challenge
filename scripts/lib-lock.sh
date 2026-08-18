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
