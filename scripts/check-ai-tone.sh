#!/usr/bin/env bash
# Curtail AI-tone gate.
#
# Passes over tracked prose:
#   1. Em-dash and en-dash. The em-dash is the most reliable AI-tone tell and
#      this project's constitution bans it outright.
#   2. A blocklist of words that read as machine-generated marketing.
#   3. Smart quotes, under --strict only.
#
# PORTABILITY, learned the hard way: this uses `git grep` and raw UTF-8 byte
# patterns rather than `mapfile` and `grep -P`. macOS ships bash 3.2, which has
# no `mapfile`, and an earlier version of this script silently scanned NOTHING
# on the dev machine while still exiting non-zero, which reads exactly like a
# working gate. `git grep` also confines the scan to tracked files for free.
#
# EXIT CODES, checked bare with no pipe on the exit path, because a pipeline
# reports its last command's status and `cmd | tail` swallows the failure that
# matters. git grep: 0 = matched, 1 = no match, >=2 = real error. An error is a
# FAILURE, never a pass, so a broken pattern cannot green the build.
#
# Escape hatch: put AITONE_IGNORE on the line.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1

PATHSPEC=('*.md' '*.mdx' '*.txt' ':!LICENSE' ':!CHANGELOG.md')

FAIL=0
SCANNED=$(git ls-files -- "${PATHSPEC[@]}" | wc -l | tr -d ' ')

if [ "$SCANNED" -eq 0 ]; then
  echo "check-ai-tone: no tracked prose files, nothing to scan"
  exit 0
fi

# run_pass <label> <pattern> [extra git-grep flags]
run_pass() {
  label="$1"
  pattern="$2"
  shift 2
  echo "==> $label"

  out=$(git grep -n "$@" -e "$pattern" -- "${PATHSPEC[@]}")
  rc=$?

  if [ "$rc" -ge 2 ]; then
    echo "  ERROR: git grep exited $rc. Counting as FAILURE, not a pass."
    FAIL=1
    return
  fi

  hits=$(printf '%s\n' "$out" | grep -v 'AITONE_IGNORE' | grep -c . )
  if [ "$rc" -eq 0 ] && [ "$hits" -gt 0 ]; then
    printf '%s\n' "$out" | grep -v 'AITONE_IGNORE' | sed 's/^/  /'
    FAIL=1
  else
    echo "  clean"
  fi
}

# Raw UTF-8 bytes: em-dash U+2014, en-dash U+2013.
run_pass "pass 1: em-dash and en-dash" "$(printf '\xe2\x80\x94\|\xe2\x80\x93')"

# Blocklist. Bare stems are avoided where they collide with real domain
# vocabulary, so this list uses full words only.
BLOCKLIST='leverage\|leverages\|leveraging\|seamless\|seamlessly\|robust\|comprehensive\|unlock\|unlocked\|unlocks\|cutting-edge\|revolutionary\|streamline\|streamlined\|streamlines\|ecosystem\|effortlessly\|delve\|elevate\|empower\|empowering\|empowers\|intuitive\|game-changing\|state-of-the-art\|best-in-class\|sophisticated\|transformative'
run_pass "pass 2: blocklist" "$BLOCKLIST" -i -w

if [ "$STRICT" -eq 1 ]; then
  run_pass "pass 3: smart quotes (strict)" "$(printf '\xe2\x80\x98\|\xe2\x80\x99\|\xe2\x80\x9c\|\xe2\x80\x9d')"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "check-ai-tone: PASSED across $SCANNED tracked prose file(s)"
  exit 0
fi
echo "check-ai-tone: FAILED"
exit 1
