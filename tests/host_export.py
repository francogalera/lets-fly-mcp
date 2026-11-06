#!/usr/bin/env python3
"""flights update --from with real git, on the host, since the core image has none. Usage: host_export.py RELEASE_PY"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile


def main():
    if not shutil.which("git"):
        print("git export: skipped, no git")
        return
    spec = importlib.util.spec_from_file_location("release", sys.argv[1])
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                      GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    with tempfile.TemporaryDirectory() as tmp:
        top = os.path.join(tmp, "checkout")
        os.mkdir(top)

        def git(*args):
            return subprocess.run(["git", "-C", top, *args], check=True, capture_output=True, text=True).stdout

        def write(name, text):
            with open(os.path.join(top, name), "w", encoding="utf-8") as f:
                f.write(text)

        def export():
            folder = tempfile.mkdtemp(dir=tmp)
            return release.export(top, folder), folder

        git("init", "-q")
        write("tracked", "old\n")
        git("add", "-A")
        git("commit", "-qm", "first")
        sha = git("rev-parse", "--short=12", "HEAD").strip()
        version, _ = export()
        assert version == sha, f"a clean checkout is {version}, not {sha}"

        write("tracked", "new\n")
        write("untracked", "new\n")
        status = git("status", "--porcelain")
        version, folder = export()
        assert version.startswith(f"{sha}-") and len(version) == len(sha) + 13, f"with changes it is {version}"
        with open(os.path.join(folder, "tracked"), encoding="utf-8") as f:
            assert f.read() == "new\n", "the change to a tracked file did not reach the version"
        assert os.path.exists(os.path.join(folder, "untracked")), "the new file did not reach the version"
        assert git("status", "--porcelain") == status, "the real index changed"
        assert export()[0] == version, "the same content got another id"
    print("git export: ok")


if __name__ == "__main__":
    main()
