#!/usr/bin/env bash
# post-format.sh — Claude Code PostToolUse hook
#
# Runs after Write and Edit tool calls. Auto-formats changed Python files
# with ruff, run via uvx since ruff is not a project dev dependency here.
#
# Input:  JSON on stdin with tool_input.file_path
# Output: Message on stdout; always exit 0 (formatting is best-effort)

set -uo pipefail

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)

[ -z "$FILE" ] && exit 0
[ -f "$FILE" ] || exit 0

case "$FILE" in
  *.py)
    if command -v uvx &>/dev/null; then
      uvx ruff format "$FILE" 2>/dev/null || true
      uvx ruff check --fix "$FILE" 2>/dev/null || true
    fi
    ;;
esac

exit 0
