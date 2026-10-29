#!/usr/bin/env bash
# Installs flights on this machine: builds the images, links the command and copies the subagent and its skill.
# Safe to run more than once. Needs Docker.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

command -v docker >/dev/null || { echo "Docker is missing" >&2; exit 1; }
mkdir -p ~/.local/bin ~/.claude/agents ~/.claude/skills/broad-search
ln -sfn "$PWD/bin/flights" ~/.local/bin/flights
# Copies, not links: whatever backs up or syncs ~/.claude keeps the files and not links into this repo
cp agent/flights.md ~/.claude/agents/flights.md
cp skills/broad-search/SKILL.md ~/.claude/skills/broad-search/SKILL.md
bin/flights build

# The subagent starts the MCP with the `flights` command, so it must be on Claude Code's PATH
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Warning: ~/.local/bin is not on PATH; add it or the subagent will not find 'flights'" >&2 ;;
esac
echo "Done: 'flights --help' in the terminal, and the flights subagent in new Claude Code sessions."
