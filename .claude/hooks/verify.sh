#!/usr/bin/env bash
# Stop hook. Before Claude finishes a turn, run the project's check command (.claude/check.sh).
# If it fails, exit 2 so Claude sees the output and keeps working.
input="$(cat)"
# Avoid an infinite loop: if we already forced a continuation once, let it stop.
if printf '%s' "$input" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
  exit 0
fi
root="${CLAUDE_PROJECT_DIR:-.}"
script="$root/.claude/check.sh"
[ -f "$script" ] || exit 0
# Skip when nothing changed (pure Q&A turns).
if command -v git >/dev/null 2>&1 && git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
  [ -z "$(git -C "$root" status --porcelain)" ] && exit 0
fi
out="$(cd "$root" && bash "$script" 2>&1)"
status=$?
if [ $status -ne 0 ]; then
  echo "Project checks failed (.claude/check.sh). Fix these before finishing:" >&2
  echo "$out" | tail -n 60 >&2
  exit 2
fi
exit 0
