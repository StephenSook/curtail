#!/usr/bin/env bash
# Exit 0 only when EVERY check on a SHA concluded success.
#
# Written after merging a red PR. The loop that preceded it PRINTED the check results and
# then ran `gh pr merge` unconditionally, so a failing "Console in a real browser" scrolled
# past and the merge went ahead. Printing is not gating. This returns a status a shell can
# branch on, and it refuses when no checks are registered yet rather than reporting success
# for a SHA nothing has looked at.
set -u
sha="${1:?usage: ci_green.sh <sha> [repo]}"
repo="${2:-StephenSook/curtail}"
for _ in $(seq 1 60); do
  out=$(gh api "repos/$repo/commits/$sha/check-runs" --jq '.check_runs[] | "\(.status)|\(.conclusion)|\(.name)"' 2>/dev/null)
  total=$(printf '%s\n' "$out" | grep -c . || true)
  pending=$(printf '%s\n' "$out" | grep -c '^in_progress\|^queued' || true)
  if [ "$total" -ge 4 ] && [ "$pending" -eq 0 ]; then break; fi
  sleep 20
done
printf '%s\n' "$out" | tr '|' ' ' | sed 's/^/  /'
if [ "${total:-0}" -lt 4 ]; then
  echo "  REFUSING: only ${total:-0} checks registered on $sha" >&2
  exit 2
fi
bad=$(printf '%s\n' "$out" | grep -vc '^completed|success|' || true)
if [ "$bad" -ne 0 ]; then
  echo "  NOT GREEN: $bad check(s) did not succeed" >&2
  exit 1
fi
echo "  green: every check succeeded"
