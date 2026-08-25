#!/bin/bash
# mkdir-based advisory lock — macOS ships no flock(1). Source, don't execute.
#   acquire_lock <target-file> [timeout_s=10] [max_age_s=0]
#                                              creates <target-file>.lock.d
#   release_lock                                removes it (also runs on EXIT)
# One lock per process; acquiring sets a trap that releases on EXIT/INT/TERM.
#
# max_age_s (default 0 = disabled, the historical behaviour) breaks a lock whose
# directory is older than max_age_s. Added for the unattended scheduler: a run
# killed by an OOM or a container restart leaves .lock.d behind with no process
# to clean it up, and on a laptop a human noticed. On a server nobody does, and
# every later run of that job blocks forever. Interactive callers should leave
# it disabled — there, a stuck lock means a live session is still working.
acquire_lock() {
  local target="$1" timeout="${2:-10}" max_age="${3:-0}" waited=0
  _LOCKDIR="${target}.lock.d"
  while ! mkdir "$_LOCKDIR" 2>/dev/null; do
    if [ "$max_age" -gt 0 ] && _lock_older_than "$_LOCKDIR" "$max_age"; then
      echo "lock: breaking stale lock $_LOCKDIR (older than ${max_age}s)" >&2
      rmdir "$_LOCKDIR" 2>/dev/null || true
      continue
    fi
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

# stat(1) is incompatible between BSD (macOS) and GNU (the container), and this
# library has to work on both.
_lock_older_than() {
  local dir="$1" max_age="$2" mtime now
  mtime="$(stat -f %m "$dir" 2>/dev/null || stat -c %Y "$dir" 2>/dev/null)" || return 1
  [ -n "$mtime" ] || return 1
  now="$(date +%s)"
  [ "$((now - mtime))" -ge "$max_age" ]
}

release_lock() {
  if [ -n "${_LOCKDIR:-}" ]; then rmdir "$_LOCKDIR" 2>/dev/null || true; _LOCKDIR=""; fi
}
