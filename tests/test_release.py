"""flights update and rollback with every command faked and the files real: the order, a failed build, the cleanup."""
import contextlib
import gzip
import importlib.util
import io
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
REAL_RUN = subprocess.run
A, B, C, D = (letter * 40 for letter in "abcd")


def load(name, home, argv=("x",)):
    """A fresh copy of a script in bin/, which reads HOME and argv when it loads."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(BIN, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(sys, "argv", list(argv)):
        spec.loader.exec_module(module)
    return module


def tar_of(path, tags, mode, prefix="", commit=None, updater=True):
    headers = {"comment": commit} if commit else {}
    files = {"tags": "\n".join(tags), **({"bin/release.py": ""} if updater else {})}
    with tarfile.open(path, mode, format=tarfile.PAX_FORMAT, pax_headers=headers) as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{prefix}{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


class Machine:
    """Answers every command release.py runs. A version's tar holds the tags of its images."""

    def __init__(self, root):
        self.root, self.log, self.images, self.in_use = root, [], set(), set()
        self.commit, self.tags, self.tree, self.head_tree = None, (), "t" * 40, "t" * 40
        self.build_fails = self.setup_fails = self.no_launcher = False
        self.updater = self.update_runs = True
        self.download = None

    def current(self):
        path = os.path.join(self.root, "current")
        return os.readlink(path) if os.path.islink(path) else None

    def __call__(self, args, **kwargs):
        args = list(args)
        name = os.path.basename(args[0])
        out, code, err = "", 0, ""
        if name == "curl":
            path = args[args.index("-o") + 1]
            if self.download is not None:
                with open(path, "wb") as f:
                    f.write(self.download)
            else:
                tar_of(path, self.tags, "w:gz", "lets-fly-mcp-main/", self.commit, self.updater)
            self.log.append("download")
        elif name == "tar":
            return REAL_RUN(args, **kwargs)
        elif name == "git":
            out = self.git(args[3:])
        elif name == "flights" and args[1] == "build":
            self.log.append(("build", self.current()))
            code = int(self.build_fails)
            if not code:
                self.images |= set(self.tags)
        elif name == "flights" and args[1] == "update":
            if self.no_launcher:
                raise FileNotFoundError(args[0])
            code = int(not self.update_runs)
            err = "SyntaxError: invalid syntax" if code else ""
        elif name == "flights" and args[1] == "images":
            with open(os.path.join(os.path.dirname(os.path.dirname(args[0])), "tags"), encoding="utf-8") as f:
                out = f.read()
        elif name == "docker" and args[1:3] == ["image", "ls"]:
            out = "\n".join(sorted(self.images))
        elif name == "docker" and args[1:3] == ["image", "rm"]:
            self.log.append(("rm", args[3:]))
            if args[3] in self.in_use:
                code, err = 1, "conflict: unable to delete"
            else:
                self.images.discard(args[3])
        elif name == "docker" and args[1:3] == ["image", "inspect"]:
            code = int(not set(args[3:]) <= self.images)
            err = "No such image" if code else ""
        elif args[2:3] and args[2].endswith("install_clients.py"):
            self.log.append(("setup", self.current()))
            code = int(self.setup_fails)
        else:
            raise AssertionError(f"unexpected command: {args}")
        return subprocess.CompletedProcess(args, code, out, err)

    def git(self, args):
        if args[:1] == ["archive"]:
            tar_of(args[2], self.tags, "w", updater=self.updater)
            return ""
        return {("rev-parse", "--show-toplevel"): "/checkout", ("write-tree",): self.tree,
                ("rev-parse", "--short=12", "HEAD"): (self.commit or "")[:12],
                ("rev-parse", "HEAD^{tree}"): self.head_tree}.get(tuple(args), "")


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="with space ")
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.release = load("release", self.home)
        self.root = self.release.ROOT
        self.machine = Machine(self.root)
        for patch in (mock.patch.object(subprocess, "run", self.machine),
                      mock.patch.object(sys, "stdout", io.StringIO()),
                      mock.patch.object(self.release.shutil, "which", return_value="/usr/bin/x")):
            patch.start()
            self.addCleanup(patch.stop)

    def version(self, commit, core=None, proxy="lets-fly-proxy:1"):
        self.machine.commit = commit
        self.machine.tags = (core or f"lets-fly-core:{commit[0]}", proxy)

    def update(self, commit, **tags):
        self.version(commit, **tags)
        self.release.update()

    def try_out(self, commit, tree, **tags):
        self.version(commit, **tags)
        self.machine.tree = tree
        self.release.update("/checkout", trial=True)

    def links(self):
        return tuple(os.readlink(os.path.join(self.root, n)) if os.path.islink(os.path.join(self.root, n)) else None
                     for n in ("current", "previous"))

    def folders(self):
        return sorted(os.listdir(os.path.join(self.root, "releases")))

    def steps(self, kind):
        return [step for step in self.machine.log if step[0] == kind]

    def test_it_builds_before_switching_and_sets_up_after(self):
        self.update(A)
        self.update(B)
        log = [step for step in self.machine.log if step[0] in ("build", "setup")]
        self.assertEqual(log, [("build", None), ("setup", "releases/aaaaaaaaaaaa"),
                               ("build", "releases/aaaaaaaaaaaa"), ("setup", "releases/bbbbbbbbbbbb")])
        self.assertEqual(os.readlink(os.path.join(self.home, ".local", "bin", "flights")),
                         os.path.join(self.root, "current", "bin", "flights"))
        self.assertTrue(os.path.exists(os.path.join(self.root, "releases", "bbbbbbbbbbbb", "tags")))

    def test_a_failed_build_changes_nothing(self):
        self.update(A)
        self.machine.log.clear()
        self.machine.build_fails = True
        with self.assertRaises(SystemExit) as stop:
            self.update(B)
        self.assertIn("nothing changed", str(stop.exception))
        self.assertEqual(self.links(), ("releases/aaaaaaaaaaaa", None))
        self.assertEqual(self.folders(), ["aaaaaaaaaaaa"])
        self.assertEqual(self.machine.log, ["download", ("build", "releases/aaaaaaaaaaaa")])

    def test_a_tar_that_names_no_commit_changes_nothing(self):
        self.update(A)
        self.machine.log.clear()
        self.version(B)
        self.machine.commit = None
        with self.assertRaises(SystemExit) as stop:
            self.release.update()
        self.assertIn("is not a tar that names its commit", str(stop.exception))
        self.assertEqual(self.links(), ("releases/aaaaaaaaaaaa", None))
        self.assertEqual(self.folders(), ["aaaaaaaaaaaa"])
        self.assertEqual(self.steps("build"), [])

    def test_a_download_that_is_not_a_tar_changes_nothing(self):
        self.update(A)
        whole = gzip.compress(b"x" * 1000)
        for body in (b"<html>captive portal</html>", whole[:30], whole[:12] + b"\xff" * 40):
            with self.subTest(body=body[:12]):
                self.machine.download = body
                with self.assertRaises(SystemExit) as stop:
                    self.update(B)
                self.assertIn("nothing changed", str(stop.exception))
                self.assertEqual(self.links(), ("releases/aaaaaaaaaaaa", None))

    def test_a_version_that_could_not_update_or_roll_back_is_refused(self):
        self.update(A)
        self.machine.log.clear()
        reasons = {"updater": "no bin/release.py", "update_runs": "SyntaxError: invalid syntax",
                   "no_launcher": "bin/flights"}
        for broken, reason in reasons.items():
            with self.subTest(broken=broken):
                working = getattr(self.machine, broken)
                setattr(self.machine, broken, not working)
                with self.assertRaises(SystemExit) as stop:
                    self.try_out(B, "1" * 40)
                setattr(self.machine, broken, working)
                self.assertIn("/checkout cannot run its own update: ", str(stop.exception))
                self.assertIn(reason, str(stop.exception))
                self.assertIn("nothing changed", str(stop.exception))
                self.assertEqual(self.links(), ("releases/aaaaaaaaaaaa", None))
                self.assertEqual(self.folders(), ["aaaaaaaaaaaa"])
                self.assertEqual(self.steps("build"), [])

    def test_a_failed_setup_leaves_the_new_version_and_cleans_nothing(self):
        self.update(A)
        self.update(B)
        self.machine.setup_fails = True
        with self.assertRaises(SystemExit):
            self.update(C)
        self.assertEqual(self.links(), ("releases/cccccccccccc", "releases/bbbbbbbbbbbb"))
        self.assertEqual(self.folders(), ["aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"])

    def test_only_the_current_and_the_previous_stay_with_their_images(self):
        self.update(A)
        self.update(B)
        self.update(C)
        self.assertEqual(self.links(), ("releases/cccccccccccc", "releases/bbbbbbbbbbbb"))
        self.assertEqual(self.folders(), ["bbbbbbbbbbbb", "cccccccccccc"])
        self.assertEqual(self.machine.images, {"lets-fly-core:b", "lets-fly-core:c", "lets-fly-proxy:1"})
        self.assertEqual(self.steps("rm"), [("rm", ["lets-fly-core:a"])])

    def test_a_damaged_folder_with_the_name_of_the_version_is_replaced(self):
        self.update(A)
        tags = os.path.join(self.root, "releases", "aaaaaaaaaaaa", "tags")
        os.remove(tags)
        self.update(A)
        self.assertTrue(os.path.exists(tags))
        self.assertEqual(self.folders(), ["aaaaaaaaaaaa"])

    def test_an_image_in_use_stays_for_a_later_cleanup_and_the_rest_goes(self):
        self.update(A, proxy="lets-fly-proxy:a")
        self.update(B)
        self.machine.in_use = {"lets-fly-core:a"}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.update(C)
        self.assertIn("kept lets-fly-core:a: conflict", out.getvalue())
        self.assertEqual(self.machine.images, {"lets-fly-core:a", "lets-fly-core:b", "lets-fly-core:c",
                                               "lets-fly-proxy:1"})
        self.machine.in_use = set()
        self.update(D)
        self.assertEqual(self.machine.images, {"lets-fly-core:c", "lets-fly-core:d", "lets-fly-proxy:1"})

    def test_the_same_version_again_keeps_the_previous_one(self):
        self.update(A)
        self.update(B)
        self.update(B)
        self.assertEqual(self.links(), ("releases/bbbbbbbbbbbb", "releases/aaaaaaaaaaaa"))

    def test_rollback_swaps_without_building_and_a_second_one_undoes_it(self):
        self.update(A)
        self.update(B)
        self.machine.log.clear()
        self.release.rollback()
        self.assertEqual(self.links(), ("releases/aaaaaaaaaaaa", "releases/bbbbbbbbbbbb"))
        self.release.rollback()
        self.assertEqual(self.links(), ("releases/bbbbbbbbbbbb", "releases/aaaaaaaaaaaa"))
        self.assertEqual(self.machine.log, [("setup", "releases/aaaaaaaaaaaa"), ("setup", "releases/bbbbbbbbbbbb")])

    def test_a_rollback_whose_setup_fails_points_at_the_setup_and_not_at_rollback(self):
        self.update(A)
        self.update(B)
        self.machine.setup_fails = True
        with self.assertRaises(SystemExit) as stop:
            self.release.rollback()
        self.assertEqual(self.links(), ("releases/aaaaaaaaaaaa", "releases/bbbbbbbbbbbb"))
        printed = shlex.split(str(stop.exception).split("run: ")[1])
        self.assertEqual(printed[-2:], [os.path.join(self.root, "current", "bin", "install_clients.py"),
                                        os.path.join(self.root, "current")])

    def test_rollback_with_no_previous_version_says_so(self):
        self.update(A)
        with self.assertRaises(SystemExit) as stop:
            self.release.rollback()
        self.assertIn("no previous version", str(stop.exception))

    def test_rollback_stops_before_switching_when_the_images_are_gone(self):
        self.update(A)
        self.update(B)
        self.machine.images.discard("lets-fly-core:a")
        self.machine.log.clear()
        with self.assertRaises(SystemExit) as stop:
            self.release.rollback()
        self.assertIn("No such image", str(stop.exception))
        self.assertEqual(shlex.split(str(stop.exception).split("run: ")[1]),
                         [os.path.join(self.root, "previous", "bin", "flights"), "build"])
        self.assertEqual(self.links(), ("releases/bbbbbbbbbbbb", "releases/aaaaaaaaaaaa"))
        self.assertEqual(self.machine.log, [])

    def test_a_trial_never_becomes_the_previous_version(self):
        self.update(A)
        self.try_out(B, "1" * 40)
        self.try_out(B, "2" * 40)
        trial = f"releases/bbbbbbbbbbbb-{'2' * 12}"
        self.assertEqual(self.links(), (trial, "releases/aaaaaaaaaaaa"))
        self.assertEqual(self.folders(), ["aaaaaaaaaaaa", f"bbbbbbbbbbbb-{'2' * 12}"])
        self.update(C)
        self.assertEqual(self.links(), ("releases/cccccccccccc", "releases/aaaaaaaaaaaa"))

    def test_a_clean_checkout_is_named_by_its_commit_alone(self):
        self.try_out(B, "t" * 40)
        self.assertEqual(self.links(), ("releases/bbbbbbbbbbbb", None))

    def test_a_trial_of_the_installed_version_leaves_it_stable(self):
        self.update(A)
        self.try_out(A, "t" * 40)
        self.update(B)
        self.assertEqual(self.links(), ("releases/bbbbbbbbbbbb", "releases/aaaaaaaaaaaa"))

    def test_updating_to_a_trial_makes_it_stable(self):
        self.update(A)
        self.try_out(B, "t" * 40)
        self.update(B)
        self.update(C)
        self.assertEqual(self.links(), ("releases/cccccccccccc", "releases/bbbbbbbbbbbb"))

    def test_an_install_counts_as_stable(self):
        self.version(A)
        self.release.update("/checkout")
        self.update(B)
        self.assertEqual(self.links(), ("releases/bbbbbbbbbbbb", "releases/aaaaaaaaaaaa"))
