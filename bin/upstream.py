"""flights upstream: what changed in fli since the commit our code derives from, read only.

Lists fli's commits after the base commit, then the diff of the fli files our code derives from,
flagging the lines that touch Google's page transport. Nothing is applied: a change worth having is
ported by hand. Usage: upstream.py [REF]
"""
import json
import os
import re
import sys
import urllib.request

API = "https://api.github.com/repos/punitarani/fli"
BASE = ""
SOURCES = {f"fli/{path}.py" for path in (
    "core/airports", "core/builders", "core/errors", "core/links", "core/parsers", "mcp/server",
    "models/google_flights/base", "models/google_flights/dates", "models/google_flights/flights",
    "search/_concurrency", "search/_decoders", "search/_helpers", "search/_proto", "search/_tfs", "search/client",
    "search/dates", "search/exceptions", "search/flights")}
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "flights")
# Where a Google change shows up first: the page URL and its tfs, the ds:1 blob, the row positions we read
TRANSPORT = re.compile(r"travel/flights|tfs|AF_initDataCallback|ds:1|sideChannel|x-goog|SOCS|impersonate|"
                       r"safe_get\(|\b(fl|row|detail|info)\[\d")


def get(path):
    request = urllib.request.Request(f"{API}/{path}", headers={"Accept": "application/vnd.github+json",
                                                              "User-Agent": "flights-upstream"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    base, ref, ours = BASE, sys.argv[1] if len(sys.argv) > 1 else "main", SOURCES
    compare = get(f"compare/{base}...{ref}")
    head = compare["commits"][-1]["sha"] if compare["commits"] else base
    print(f"fli {base[:7]} -> {ref} ({head[:7]}): {compare['ahead_by']} commits")
    for commit in compare["commits"]:
        print(f"  {commit['sha'][:7]} {commit['commit']['committer']['date'][:10]} {commit['commit']['message'].splitlines()[0]}")
    changed = [f for f in compare.get("files", []) if f["filename"] in ours]
    if not changed:
        print(f"None of the {len(ours)} fli files we derive from changed.")
        return
    print(f"Files we derive from that changed ({len(changed)} of {len(ours)}):")
    patches = []
    for f in changed:
        patch = f.get("patch", "")
        touched = [line for line in patch.splitlines() if line[:1] in "+-" and TRANSPORT.search(line)]
        print(f"  {f['filename']}  +{f['additions']} -{f['deletions']}" + (f"  TRANSPORT ({len(touched)} lines)" if touched else ""))
        patches.append(f"--- {f['filename']}\n{patch}\n")
    os.makedirs(STATE, exist_ok=True)
    path = os.path.join(STATE, f"upstream-{head[:12]}.diff")
    with open(path, "w") as out:
        out.writelines(patches)
    print(f"Diff of those files: {path}")


main()
