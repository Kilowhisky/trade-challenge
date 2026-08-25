#!/bin/bash
# Advance the server's checkout to origin/deploy — but only if the rule suite
# still passes on the new commit.
#
# Why a `deploy` branch and not `main`: an auto-pull of main would put an
# unreviewed commit into a live trading system the moment it is pushed. `deploy`
# is fast-forwarded deliberately by Chris, so "what the server runs" is an
# explicit act rather than a side effect of typing `git push`.
#
# Why the suite is the gate: §4.5 already defines a check-consistency FAIL as
# "a defect to fix before trading, not a warning to note". On a laptop a human
# reads that and stops. On an unattended server nobody does, so the only way to
# honour the rule without a human present is to refuse to adopt the commit.
# Rolling back to the last good SHA keeps the account trading yesterday's
# verified rules rather than today's unverified ones.
#
# Exit: 0 up to date or updated cleanly · 1 gate failed and was rolled back
#     · 2 could not run (dirty tree, no remote, etc).
set -uo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root" || exit 2

branch="${TC_DEPLOY_BRANCH:-deploy}"
log_dir="$repo_root/status/cron"; mkdir -p "$log_dir"
ledger="$log_dir/deployed.jsonl"
et() { TZ=America/New_York date "$@"; }
say() { printf 'repo-update: %s\n' "$*" >&2; }
notify() { "$repo_root/scripts/discord-notify.sh" "$1" >/dev/null 2>&1 || true; }

record() { # from to verdict detail
  if command -v jq >/dev/null 2>&1; then
    jq -nc --arg t "$(et '+%Y-%m-%d %H:%M:%S %Z')" --arg f "$1" --arg to "$2" \
           --arg v "$3" --arg d "${4:-}" \
      '{at:$t,from:$f,to:$to,verdict:$v,detail:$d}' >> "$ledger"
  fi
}

command -v git >/dev/null 2>&1 || { say "git not found"; exit 2; }

# A dirty tree on a deploy target means somebody edited in place. Refuse rather
# than clobber: git checkout would silently discard their work.
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  say "working tree has uncommitted changes to tracked files — refusing to update"
  notify "⚠️ repo-update refused: the server checkout has uncommitted changes to tracked files. Scheduled jobs continue on the current commit."
  exit 2
fi

git fetch --quiet origin "$branch" 2>/dev/null || { say "fetch of origin/$branch failed"; exit 2; }

current="$(git rev-parse HEAD)"
target="$(git rev-parse "origin/$branch")" || { say "no origin/$branch"; exit 2; }

if [ "$current" = "$target" ]; then
  exit 0                       # quiet: this is the common case, every job, all day
fi

say "updating ${current:0:8} -> ${target:0:8}"
git checkout --quiet --detach "$target" || { say "checkout failed"; exit 2; }

# --- the gate -------------------------------------------------------------
gate_fail=""
if ! ./scripts/check-consistency.sh >/tmp/repo-update-gate.$$ 2>&1; then
  gate_fail="check-consistency"
elif ! ./scripts/test-pre-order-check.sh >>/tmp/repo-update-gate.$$ 2>&1; then
  gate_fail="test-pre-order-check"
fi
gate_out="$(tail -25 /tmp/repo-update-gate.$$ 2>/dev/null)"; rm -f /tmp/repo-update-gate.$$

if [ -n "$gate_fail" ]; then
  say "GATE FAILED ($gate_fail) — rolling back to ${current:0:8}"
  git checkout --quiet --detach "$current" || say "ROLLBACK FAILED — server is on an ungated commit"
  record "$current" "$target" "rejected" "$gate_fail"
  notify "🚫 **Deploy rejected** — \`${target:0:8}\` failed \`$gate_fail\`. Rolled back to \`${current:0:8}\`; jobs continue on the previous rules.
\`\`\`
$gate_out
\`\`\`"
  exit 1
fi

record "$current" "$target" "adopted" ""
notify "⬆️ Server updated to \`${target:0:8}\` — rule suite passed. $(git log -1 --pretty=%s "$target")"
say "adopted ${target:0:8}"
exit 0
