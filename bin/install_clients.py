#!/usr/bin/env python3
"""Points the MCP clients found on this machine at bin/flights: Claude Desktop, Codex, and a plugin in the personal
marketplace that ChatGPT Desktop installs. Then runs the link server as a service of the user. Run by install.sh;
safe to run again. Python 3.9, as macOS ships it. Usage: install_clients.py REPO"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time

REPO = os.path.realpath(sys.argv[1])
FLIGHTS = os.path.join(REPO, "bin", "flights")
HOME = os.path.expanduser("~")
# Clients other than Claude Code start servers with a short environment, so the Docker endpoint goes with them
DOCKER = {k: os.environ[k] for k in ("DOCKER_HOST", "DOCKER_CONTEXT") if os.environ.get(k)}
SERVER = {"command": FLIGHTS, "args": ["mcp"], **({"env": DOCKER} if DOCKER else {})}
sys.path.insert(0, os.path.join(REPO, "src"))
from letsfly.links import BASE, LABEL, UNIT  # noqa: E402  stdlib only, so it runs on the host
# The service runs the installed command, which install.sh points at this checkout, not this checkout's path
SERVE = [os.path.join(HOME, ".local", "bin", "flights"), "links"]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f"{path}.tmp", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(f"{path}.tmp", path)


def merge_json(path, update):
    """Applies update to the JSON at path, keeping everything else it holds."""
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    update(data)
    write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def claude_desktop():
    folder = os.path.join(HOME, "Library", "Application Support", "Claude") if sys.platform == "darwin" \
        else os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "Claude")
    if not os.path.isdir(folder):
        return "Claude Desktop: not installed, skipped"
    merge_json(os.path.join(folder, "claude_desktop_config.json"),
               lambda d: d.setdefault("mcpServers", {}).__setitem__("flights", SERVER))
    return "Claude Desktop: flights added; restart the app"


def codex():
    if not shutil.which("codex"):
        return "Codex: not installed, skipped"
    subprocess.run(["codex", "mcp", "add", "flights", "--", FLIGHTS, "mcp"], check=True, capture_output=True)
    # codex mcp add has no flag for env_vars, which passes variables through Codex's filtered environment
    path = os.path.join(os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex"), "config.toml")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    at = lines.index("[mcp_servers.flights]") + 1
    table = lines[at:next((i for i in range(at, len(lines)) if lines[i].startswith("[")), len(lines))]
    if not any(line.startswith("env_vars") for line in table):
        lines.insert(at, 'env_vars = ["DOCKER_HOST", "DOCKER_CONTEXT"]')
        write(path, "\n".join(lines) + "\n")
    skills(os.path.join(HOME, ".agents", "skills"))
    return "Codex: flights added, with the flights and broad-search skills"


def skills(folder):
    """The agent prompt becomes the flights skill, so outside Claude Code it has one source too."""
    with open(os.path.join(REPO, "agent", "flights.md"), encoding="utf-8") as f:
        _, front, body = f.read().split("---\n", 2)
    description = next(line for line in front.splitlines() if line.startswith("description: "))
    write(os.path.join(folder, "flights", "SKILL.md"), f"---\nname: flights\n{description}\n---\n{body}")
    shutil.copyfile(os.path.join(REPO, "skills", "broad-search", "SKILL.md"),
                    _made(os.path.join(folder, "broad-search", "SKILL.md")))


def plugin():
    """The plugin lives in ~/.codex/plugins, where the personal marketplace of Codex and ChatGPT Desktop points."""
    root = os.path.join(HOME, ".codex", "plugins", "flights")
    shutil.copyfile(os.path.join(REPO, "plugin", "plugin.json"), _made(os.path.join(root, "plugin.json")))
    write(os.path.join(root, "mcp.json"), json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"flights": {"type": "stdio", **SERVER}}}, indent=2) + "\n")
    skills(os.path.join(root, "skills"))
    entry = {"name": "flights", "source": {"source": "local", "path": "./.codex/plugins/flights"},
             "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}

    def add(market):
        market.setdefault("name", "personal")
        market["plugins"] = [p for p in market.get("plugins", []) if p.get("name") != "flights"] + [entry]
    merge_json(os.path.join(HOME, ".agents", "plugins", "marketplace.json"), add)
    return "Plugin: flights in the personal marketplace; install it from ChatGPT Desktop's Plugins Directory"


def links():
    """Started at login and again if it fails: systemd on Linux, launchd on macOS. Restarted, so an update counts."""
    return launchd() if sys.platform == "darwin" else systemd()


def systemd():
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "systemd", "user", UNIT)
    write(path, f"[Unit]\nDescription=flights: short links on {BASE}\n\n"
                "[Service]\nExecStart=%h/.local/bin/flights links\nRestart=on-failure\nRestartSec=2\n\n"
                "[Install]\nWantedBy=default.target\n")
    for step in (["daemon-reload"], ["enable", UNIT], ["restart", UNIT]):
        error = _run("systemctl", "--user", *step)
        if error:
            return f"Links: wrote {path}, but systemctl --user {step[0]} failed: {error}"
    return f"Links: {BASE}, served by the systemd user service {UNIT}"


def launchd():
    """Written to the launchd docs, not yet run on a Mac."""
    path = os.path.join(HOME, "Library", "LaunchAgents", f"{LABEL}.plist")
    write(path, plistlib.dumps({"Label": LABEL, "ProgramArguments": SERVE, "RunAtLoad": True,
                                "KeepAlive": True}).decode())
    domain = f"gui/{os.getuid()}"
    _run("launchctl", "bootout", domain, path)  # fails when it was not loaded, which is fine
    for _ in range(10):  # bootout returns before the job is gone, and bootstrap fails until it is
        error = _run("launchctl", "bootstrap", domain, path)
        if not error:
            return f"Links: {BASE}, served by the launchd agent {LABEL}"
        time.sleep(0.5)
    return f"Links: wrote {path}, but launchctl bootstrap failed: {error}"


def _run(*args):
    """The error of a command, or None when it worked."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    return ((r.stderr or r.stdout).strip() or f"exit {r.returncode}") if r.returncode else None


def _made(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


if __name__ == "__main__":
    for setup in (claude_desktop, codex, plugin, links):
        print(setup())
