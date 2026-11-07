"""The setup of a version: clients keep following current, and one that fails does not stop the others."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from test_release import load


class InstallClientsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        root = os.path.join(self.home, ".local", "share", "flights")
        version = os.path.join(root, "releases", "v1")
        for path, text in (("agent/flights.md", "---\ndescription: flights\n---\nbody\n"),
                           ("skills/broad-search/SKILL.md", "skill\n"), ("plugin/plugin.json", "{}\n")):
            os.makedirs(os.path.dirname(os.path.join(version, path)), exist_ok=True)
            with open(os.path.join(version, path), "w", encoding="utf-8") as f:
                f.write(text)
        self.current = os.path.join(root, "current")
        os.symlink(os.path.join("releases", "v1"), self.current)
        self.clients = load("install_clients", self.home, ("x", self.current))
        self.calls = []
        env = {"HOME": self.home, "XDG_CONFIG_HOME": "", "CODEX_HOME": ""}
        for patch in (mock.patch.dict(os.environ, env), mock.patch.object(self.clients.sys, "platform", "linux"),
                      mock.patch.object(subprocess, "run", self.fake_run)):
            patch.start()
            self.addCleanup(patch.stop)
        self.codex_fails = False

    def fake_run(self, args, **kwargs):
        self.calls.append(list(args))
        code = int(self.codex_fails and args[0] == "codex")
        return subprocess.CompletedProcess(args, code, "", "codex broke" if code else "")

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def main(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.clients.main()
        return out.getvalue()

    def test_clients_keep_current_and_not_the_version_it_points_at(self):
        self.main()
        with open(self.path(".codex", "plugins", "flights", "mcp.json"), encoding="utf-8") as f:
            server = json.load(f)["mcpServers"]["flights"]
        self.assertEqual(server["command"], os.path.join(self.current, "bin", "flights"))

    def test_a_client_that_fails_is_skipped_and_the_rest_still_run(self):
        os.makedirs(self.path(".config", "Claude"))
        with open(self.path(".config", "Claude", "claude_desktop_config.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.codex_fails = True
        with mock.patch.object(self.clients.shutil, "which", return_value="/usr/bin/codex"):
            out = self.main()
        self.assertIn("claude_desktop: skipped: ", out)
        self.assertIn("claude_desktop_config.json", out)
        self.assertIn("Codex: skipped, codex mcp add failed: codex broke; it keeps what it had", out)
        self.assertTrue(os.path.exists(self.path(".codex", "plugins", "flights", "mcp.json")))
        self.assertIn(["systemctl", "--user", "restart", "flights-links.service"], self.calls)
        self.assertTrue(os.path.exists(self.path(".claude", "agents", "flights.md")))

    def test_a_client_config_of_another_shape_is_skipped_and_the_rest_still_run(self):
        os.makedirs(self.path(".config", "Claude"))
        with open(self.path(".config", "Claude", "claude_desktop_config.json"), "w", encoding="utf-8") as f:
            f.write("[]")
        os.makedirs(self.path(".agents", "plugins"))
        with open(self.path(".agents", "plugins", "marketplace.json"), "w", encoding="utf-8") as f:
            f.write('{"plugins": ["x"]}')
        out = self.main()
        self.assertIn("claude_desktop: skipped: ", out)
        self.assertIn("plugin: skipped: ", out)
        self.assertIn("marketplace.json", out)
        self.assertIn(["systemctl", "--user", "restart", "flights-links.service"], self.calls)

    def test_an_agent_with_no_description_skips_the_clients_that_need_it(self):
        with open(os.path.join(self.current, "agent", "flights.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: flights\n---\nbody\n")
        out = self.main()
        self.assertIn("plugin: skipped: ", out)
        self.assertIn("no description line", out)
        self.assertIn(["systemctl", "--user", "restart", "flights-links.service"], self.calls)

    def test_a_client_that_is_not_installed_is_skipped_without_trying(self):
        with mock.patch.object(self.clients.shutil, "which", return_value=None):
            out = self.main()
        self.assertIn("Claude Desktop: not installed, skipped", out)
        self.assertIn("Codex: not installed, skipped", out)
        self.assertFalse([call for call in self.calls if call[0] == "codex"])

    def test_a_failure_copying_for_claude_code_stops_the_setup(self):
        with open(self.path(".claude"), "w", encoding="utf-8") as f:
            f.write("a file where the folder goes")
        with self.assertRaises(OSError):
            self.main()
        self.assertEqual(self.calls, [])
