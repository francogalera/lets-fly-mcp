#!/usr/bin/env python3
"""flights update and rollback. Each version lives apart from any checkout, in ~/.local/share/flights/releases/<id>,
with the symlinks current and previous. Python 3.9, as macOS ships it, and the standard library only.
Usage: release.py update [--from PATH] | rollback | install PATH"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zlib

TARBALL = "https://github.com/francogalera/lets-fly-mcp/archive/refs/heads/main.tar.gz"
HOME = os.path.expanduser("~")
# Not XDG_DATA_HOME: the service and some clients start without it, and every caller must find the same folder
ROOT = os.path.join(HOME, ".local", "share", "flights")
RELEASES = os.path.join(ROOT, "releases")
CURRENT, PREVIOUS = os.path.join(ROOT, "current"), os.path.join(ROOT, "previous")
COMMAND = os.path.join(HOME, ".local", "bin", "flights")
# Marks a version from `update --from`: what replaces it does not keep it as previous, so rollback lands on a stable one
TRIAL = ".trial"


def run(*args, **kwargs):
    """The output of a command, or a stop with its error."""
    r = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if r.returncode:
        sys.exit(f"flights: {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def need(*tools):
    for tool in tools:
        if not shutil.which(tool):
            sys.exit(f"flights: {tool} is missing")


def target(link_path):
    return os.readlink(link_path) if os.path.islink(link_path) else None


def link(to, path):
    """Atomic, so nothing that starts meanwhile finds the link missing."""
    if os.path.lexists(f"{path}.tmp"):
        os.remove(f"{path}.tmp")
    os.symlink(to, f"{path}.tmp")
    os.replace(f"{path}.tmp", path)


def commit_of(path):
    try:
        with tarfile.open(path) as tar:
            return tar.pax_headers.get("comment", "")
    except (tarfile.TarError, EOFError, zlib.error):
        return ""


def download(folder):
    """Main from GitHub, so no copy of the repo stays here. The tar names its commit, so the two cannot disagree."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "main.tar.gz")
        run("curl", "-fsSL", "-o", path, "--connect-timeout", "20", "--max-time", "120", TARBALL)
        sha = commit_of(path)
        if not re.fullmatch("[0-9a-f]{40}", sha):
            sys.exit(f"flights: {TARBALL} is not a tar that names its commit; nothing changed")
        # tar, not tarfile: the safe extraction filter is missing from the Python macOS ships
        run("tar", "-xzf", path, "--strip-components=1", "-C", folder)
    return sha[:12]


def export(source, folder):
    """The checkout as it is, new files included, through an index of its own: the real one stays as it was."""
    top = run("git", "-C", source, "rev-parse", "--show-toplevel")
    git = ("git", "-C", top)
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        run(*git, "read-tree", "HEAD", env=env)
        run(*git, "add", "-A", env=env)
        tree = run(*git, "write-tree", env=env)
        path = os.path.join(tmp, "tree.tar")
        run(*git, "archive", "-o", path, tree)
        run("tar", "-xf", path, "-C", folder)
    sha = run(*git, "rev-parse", "--short=12", "HEAD")
    return sha if tree == run(*git, "rev-parse", "HEAD^{tree}") else f"{sha}-{tree[:12]}"


def why_it_cannot_update(folder):
    """Installed, a version that cannot run its own update and rollback would leave no way out."""
    # release.py first: the launcher of a version without it would take this for a real update
    if not os.path.exists(os.path.join(folder, "bin", "release.py")):
        return "it has no bin/release.py"
    try:
        r = subprocess.run([os.path.join(folder, "bin", "flights"), "update", "--help"], capture_output=True, text=True)
    except OSError as e:
        return str(e)
    output = (r.stderr or r.stdout).strip().splitlines()
    return (output[-1] if output else f"exit {r.returncode}") if r.returncode else None


def place(folder, version, trial):
    """The folder becomes releases/<version>, always the fresh copy: one with that name may be damaged or half gone."""
    path = os.path.join(RELEASES, version)
    stable = os.path.isdir(path) and not os.path.exists(os.path.join(path, TRIAL))
    if trial and not stable:
        open(os.path.join(folder, TRIAL), "w").close()
    if os.path.isdir(path):
        os.rename(path, tempfile.mkdtemp(prefix=".old-", dir=RELEASES))
    os.rename(folder, path)


def switch(version):
    new = os.path.join("releases", version)
    old = target(CURRENT)
    if old and old != new and not os.path.exists(os.path.join(ROOT, old, TRIAL)):
        link(old, PREVIOUS)
    link(new, CURRENT)
    if target(PREVIOUS) == new:
        os.remove(PREVIOUS)


def activate():
    """Run from the version just switched to, whose setup knows what it needs."""
    os.makedirs(os.path.dirname(COMMAND), exist_ok=True)
    link(os.path.join(CURRENT, "bin", "flights"), COMMAND)
    setup = [sys.executable, "-I", os.path.join(CURRENT, "bin", "install_clients.py"), CURRENT]
    if subprocess.run(setup).returncode:
        # Not "run it again": a second rollback would swap back, away from the version it just reached
        sys.exit(f"flights: the version is in place but its setup failed; fix that and run: {shlex.join(setup)}")


def prune():
    kept = [p for p in (CURRENT, PREVIOUS) if os.path.isdir(p)]
    versions = {os.path.realpath(p) for p in kept}
    tags = {tag for p in kept for tag in run(os.path.join(p, "bin", "flights"), "images").split()}
    for name in os.listdir(RELEASES):
        path = os.path.join(RELEASES, name)
        if os.path.realpath(path) not in versions:
            shutil.rmtree(path, ignore_errors=True)
    listed = run("docker", "image", "ls", "--filter", "reference=lets-fly-core", "--filter", "reference=lets-fly-proxy",
                 "--format", "{{.Repository}}:{{.Tag}}")
    for tag in sorted(set(listed.split()) - tags):
        # Never --force: docker refuses the image of an open session, which goes in a later cleanup
        r = subprocess.run(["docker", "image", "rm", tag], capture_output=True, text=True)
        if r.returncode:
            print(f"flights: kept {tag}: {r.stderr.strip()}", flush=True)


def update(source=None, trial=False):
    need("docker", "tar", "curl" if source is None else "git")
    os.makedirs(RELEASES, exist_ok=True)
    folder = tempfile.mkdtemp(prefix=".new-", dir=RELEASES)
    try:
        version = download(folder) if source is None else export(source, folder)
        problem = why_it_cannot_update(folder)
        if problem:
            sys.exit(f"flights: {source or TARBALL} cannot run its own update: {problem}; nothing changed")
        print(f"flights: building {version}", flush=True)
        if subprocess.run([os.path.join(folder, "bin", "flights"), "build"]).returncode:
            sys.exit("flights: the build failed; nothing changed")
        place(folder, version, trial)
        switch(version)
        activate()
        prune()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def rollback():
    need("docker")
    back, here = target(PREVIOUS), target(CURRENT)
    if not here or not back or not os.path.isdir(os.path.join(ROOT, back)):
        sys.exit("flights: there is no previous version")
    # Containers run with --pull never: without its images the version would fail later, in every client
    images = run(os.path.join(ROOT, back, "bin", "flights"), "images").split()
    r = subprocess.run(["docker", "image", "inspect", *images], capture_output=True, text=True)
    if r.returncode:
        build = shlex.join([os.path.join(PREVIOUS, "bin", "flights"), "build"])
        sys.exit(f"flights: cannot go back to {os.path.basename(back)}: {r.stderr.strip()}. Rollback builds nothing; "
                 f"to build them, run: {build}")
    link(back, CURRENT)
    link(here, PREVIOUS)
    activate()


def main():
    cli = argparse.ArgumentParser(prog="flights", description="Installs versions of flights apart from any checkout.")
    sub = cli.add_subparsers(dest="command", required=True)
    up = sub.add_parser("update", help="install the latest version, or with --from a checkout to try it")
    up.add_argument("--from", dest="source", metavar="PATH", help="the checkout, new files and changes included")
    sub.add_parser("rollback", help="back to the previous version, without building")
    sub.add_parser("install", help="install a checkout as a stable version, as install.sh does").add_argument("source")
    args = cli.parse_args()
    if args.command == "rollback":
        rollback()
    else:
        update(args.source, trial=args.command == "update" and args.source is not None)
    previous = target(PREVIOUS)
    print(f"flights: on {os.path.basename(target(CURRENT))}, "
          f"{'previous ' + os.path.basename(previous) if previous else 'no previous version'}")


if __name__ == "__main__":
    main()
