#!/bin/bash
# Commit and push whatever a scheduled job just wrote to the PRIVATE data repo.
#
# research/, status/ and trade-log.csv are gitignored in the public repo (§7.1,
# purged from history 2026-08-17). Without this, output written on the server
# would never reach Chris — a pre-open brief nobody can read is not a brief.
#
# This also repairs something §7.1 explicitly mourns: "git is no longer the
# audit trail for order flow… append-only by convention, on one machine, with
# no external witness and nothing preventing a quiet retroactive edit." A
# private sidecar restores the external witness without publishing order flow.
#
# Usage: scripts/sidecar-sync.sh "<commit subject>"
# Exit: 0 pushed, or nothing to do · 1 push failed (caller treats as non-fatal —
#       losing a sync must never fail the research run that produced the data).
set -uo pipefail

subject="${1:-scheduled run}"
say() { printf 'sidecar-sync: %s\n' "$*" >&2; }

# Find the store by following the symlink the repo already has, rather than
# requiring a configured path. The link is the single source of truth for where
# the data lives, it is created by bootstrap-server.sh, and resolving it means
# this works unchanged on the laptop (an absolute host path) and in the
# container (/data) with no env var to forget.
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
if [ -n "${TC_DATA_DIR_ABS:-}" ]; then
  data_dir="$TC_DATA_DIR_ABS"
elif [ -L "$repo_root/status" ]; then
  data_dir="$(dirname "$(cd "$(dirname "$(readlink "$repo_root/status")")" && pwd)/$(basename "$(readlink "$repo_root/status")")")"
else
  data_dir="/data"
fi

# Not deployed yet, or running on the laptop: silently do nothing. This script
# must be safe to call unconditionally from scheduled-run.sh.
[ -d "$data_dir/.git" ] || { say "no git repo at $data_dir — skipping"; exit 0; }

cd "$data_dir" || exit 0

if [ -z "$(git status --porcelain)" ]; then
  say "nothing to commit"
  exit 0
fi

git add -A || { say "git add failed"; exit 1; }
git -c user.name="trade-challenge server" \
    -c user.email="server@trade-challenge.local" \
    commit -q -m "$subject" || { say "commit failed"; exit 1; }

# --rebase, not merge: the Mac may have pulled and pushed its own session notes
# between our jobs, and a merge commit in an audit trail is noise.
#
# A FAILED rebase must not be left in place. On 2026-08-25 a laptop `git add -A`
# committed the deletion of five status/cron/ files that only the server writes
# (they were absent from the Mac's working tree), the next sync hit modify/delete
# on three of them, and this line's `|| say WARN` walked on — leaving the store
# mid-rebase on a detached HEAD. Every subsequent job would have committed INTO
# that state and been unable to push: the audit trail stops silently, which is
# the one failure mode a sidecar exists to prevent.
#
# So: abort, restore a clean branch, and say so loudly. Losing one sync is
# survivable and self-heals on the next run; a wedged repo is not.
if ! git pull --rebase --quiet 2>/dev/null; then
  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort 2>/dev/null \
      && say "pull --rebase CONFLICTED; aborted and left the branch clean" \
      || say "pull --rebase CONFLICTED and the abort FAILED — the store needs a human"
    "$repo_root/scripts/discord-notify.sh" \
      "🚨 **Sidecar store conflict** — \`$subject\` is committed on the server but could not be rebased onto origin. The rebase was aborted so jobs keep working, but **the store is diverged and nothing is pushing.** Reconcile it by hand." \
      >/dev/null 2>&1 || true
    exit 1
  fi
  say "WARN pull --rebase failed (no rebase in progress); pushing anyway may be rejected"
fi

if git push --quiet 2>/dev/null; then
  say "pushed: $subject"
  exit 0
fi

say "push failed — the commit is local on the server and will go with the next sync"
exit 1
