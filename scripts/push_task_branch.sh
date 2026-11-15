#!/usr/bin/env bash
# A fail-closed wrapper for the single routine GitHub publication operation.
# Call from the root of an assigned task worktree, without arguments.
set -euo pipefail

fail() {
  printf 'task-branch push refused: %s\n' "$1" >&2
  exit 2
}

[[ "$#" -eq 0 ]] || fail "unexpected arguments"

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "not a Git worktree"
[[ "$(pwd -P)" == "$(cd "$repo_root" && pwd -P)" ]] || fail "run from the task worktree root"

branch="$(git symbolic-ref --quiet --short HEAD)" || fail "detached HEAD"
# Accepted task naming convention from AGENTS.md; main/master and arbitrary
# branches are explicitly excluded even when they contain unpublished commits.
[[ "$branch" =~ ^t-[0-9]+[a-z]?(-[a-z0-9][a-z0-9-]*)?$ ]] || fail "not a dedicated t-XXX task branch"

origin_url="$(git remote get-url --push origin 2>/dev/null)" || fail "origin push remote is missing"
case "$origin_url" in
  "git@github.com:Dannymor94/tg_bot_masterskay" | \
  "git@github.com:Dannymor94/tg_bot_masterskay.git" | \
  "ssh://git@github.com/Dannymor94/tg_bot_masterskay" | \
  "ssh://git@github.com/Dannymor94/tg_bot_masterskay.git" | \
  "https://github.com/Dannymor94/tg_bot_masterskay" | \
  "https://github.com/Dannymor94/tg_bot_masterskay.git")
    ;;
  *)
    fail "origin is not the approved GitHub repository"
    ;;
esac

# Publishing a committed, clean candidate keeps uncommitted work out of the
# decision and avoids ambiguous handoffs. Untracked files do not enter push.
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail "tracked worktree changes remain; commit or restore them first"

# No flag or refspec is accepted from the caller; this never pushes to main.
git push -u origin HEAD
