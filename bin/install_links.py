#!/usr/bin/env python3
"""Installs the link server as a service of the user, started at login and again if it fails: systemd on Linux,
launchd on macOS. Run by install.sh; safe to run again, and it restarts the server so an update takes effect.
Python 3.9, as macOS ships it. Usage: install_links.py REPO"""
import os
import plistlib
import shlex
import subprocess
import sys
import time

REPO = os.path.realpath(sys.argv[1])
sys.path.insert(0, os.path.join(REPO, "src"))
from letsfly.links import BASE, LABEL, UNIT  # noqa: E402  stdlib only, so it runs on the host

SERVE = [sys.executable, "-I", os.path.join(REPO, "src", "letsfly", "links.py"), "serve",
         os.path.join(REPO, "local", "flight-links")]


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f"{path}.tmp", "wb") as f:
        f.write(data)
    os.replace(f"{path}.tmp", path)


def run(*args):
    """The error of a command, or None when it worked."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    return ((r.stderr or r.stdout).strip() or f"exit {r.returncode}") if r.returncode else None


def systemd():
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "systemd", "user", UNIT)
    write(path, (f"[Unit]\nDescription=flights: short links on {BASE}\n\n"
                 f"[Service]\nExecStart={shlex.join(SERVE).replace('%', '%%')}\nRestart=on-failure\nRestartSec=2\n\n"
                 "[Install]\nWantedBy=default.target\n").encode())
    for step in (["daemon-reload"], ["enable", UNIT], ["restart", UNIT]):
        error = run("systemctl", "--user", *step)
        if error:
            return f"Links: wrote {path}, but systemctl --user {step[0]} failed: {error}"
    return f"Links: {BASE}, served by the systemd user service {UNIT}"


def launchd():
    """Written to the launchd docs, not yet run on a Mac."""
    path = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
    write(path, plistlib.dumps({"Label": LABEL, "ProgramArguments": SERVE, "RunAtLoad": True, "KeepAlive": True}))
    domain = f"gui/{os.getuid()}"
    run("launchctl", "bootout", domain, path)  # fails when it was not loaded, which is fine
    for _ in range(10):  # bootout returns before the job is gone, and bootstrap fails until it is
        error = run("launchctl", "bootstrap", domain, path)
        if not error:
            return f"Links: {BASE}, served by the launchd agent {LABEL}"
        time.sleep(0.5)
    return f"Links: wrote {path}, but launchctl bootstrap failed: {error}"


if __name__ == "__main__":
    print(launchd() if sys.platform == "darwin" else systemd())
