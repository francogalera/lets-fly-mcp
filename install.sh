#!/usr/bin/env bash
# Installs this folder as a version of flights: its images, the command, the subagent and its skill, Claude Desktop,
# Codex and ChatGPT Desktop, and the link server as a service of the user. From then on `flights update` takes over,
# and this folder can go unless a client failed. Safe to run more than once. Needs Docker, git, curl and python3.
set -euo pipefail
cd "$(dirname "$0")"

python3 -I bin/release.py install "$PWD"

# The subagent starts the MCP with the `flights` command, so it must be on Claude Code's PATH
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Warning: ~/.local/bin is not on PATH; add it or the subagent will not find 'flights'" >&2 ;;
esac
echo "Done: 'flights --help' in the terminal, the flights subagent in new Claude Code sessions, the clients and links above."
