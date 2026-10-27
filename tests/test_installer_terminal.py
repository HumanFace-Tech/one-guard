import os
import pty
import re
import select
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".test-artifacts"
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


class Terminal:
    def __init__(self, root, source, release, bootstrap=False):
        self.root = root
        environment = dict(os.environ)
        environment.update({"HOME": str(root), "HERMES_HOME": str(root / "data"),
                            "HERMES_DIR": "", "PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin",
                            "TERM": "xterm-256color"})
        environment.pop("ONE_GUARD_TOKEN", None)
        script = root / "run.py"
        stub = ("import contextlib\n"
                "available = m.installer_ui.rich_available\n"
                "state = [False]\n"
                "m.installer_ui.rich_available = lambda: state[0] and available()\n"
                "@contextlib.contextmanager\n"
                "def provision():\n"
                "    state[0] = True\n"
                "    yield True\n"
                "m.installer_bootstrap.provision = provision\n") if bootstrap else ""
        script.write_text(
            "import importlib.util, sys, pathlib\n"
            f"spec = importlib.util.spec_from_file_location('i', {str(ROOT / 'scripts/install.py')!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"m.RELEASE = pathlib.Path({str(release)!r})\n"
            + stub +
            f"sys.exit(m.main(['--hermes-dir', {str(source)!r}]))\n")
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(root)
            os.execve(sys.executable, [sys.executable, str(script)], environment)
        self.transcript = ""

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            readable, _, _ = select.select([self.fd], [], [], 0.1)
            if readable:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                self.transcript += ANSI.sub("", data.decode(errors="replace")).replace("\r", "")

    def send(self, data, wait=1.0):
        for byte in data:
            os.write(self.fd, bytes([byte]))
            time.sleep(0.015)
        self.pump(wait)

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass


class TerminalWizardTests(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="terminal-", dir=ARTIFACTS)
        self.addCleanup(lambda: self.assertFalse(Path(self.temp.name).exists()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assertEqual(self.root.parent, ARTIFACTS)
        self.source = self.root / "source"
        (self.source / "hermes_cli").mkdir(parents=True)
        (self.source / "hermes_cli/__init__.py").write_text("")
        (self.source / "hermes").write_text("")
        self.release = self.root / "release"
        self.release.mkdir()
        (self.release / "plugin.yaml").write_text("name: one-guard\n")
        (self.release / "__init__.py").write_text("VERSION = 1\n")

    def start(self):
        terminal = Terminal(self.root, self.source, self.release)
        self.addCleanup(terminal.close)
        terminal.pump(2.5)
        return terminal

    def test_arrow_navigation_selects_system_one_and_installs(self):
        terminal = self.start()
        terminal.send(b"\r", 1.2)
        terminal.send(b"\x1b[B", 0.7)
        terminal.send(b"\r", 1.0)
        terminal.send(b"\x1b[B", 0.7)
        terminal.send(b"\r", 1.0)
        terminal.send(b"http://192.0.2.1:9292/v1/systemone\r", 1.6)
        terminal.send(b"h2o-lightning-4b\r", 1.4)
        terminal.send(b"\x1b[B", 0.7)
        terminal.send(b"\r", 1.0)
        terminal.send(b"60\r", 1.2)
        terminal.send(b"0.5\r", 1.2)
        terminal.send(b"\r", 1.4)
        terminal.send(b"\r", 2.5)
        import yaml
        settings = yaml.safe_load((self.root / "data/config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertEqual(settings["protocol"], "systemone")
        self.assertEqual(settings["endpoint"], "http://192.0.2.1:9292/v1/systemone")
        self.assertEqual(settings["judge_model"], "h2o-lightning-4b")
        self.assertEqual(settings["timeout"], 60)
        self.assertTrue(settings["allow_insecure_http"])

    def test_consent_bootstrap_then_arrow_wizard(self):
        terminal = Terminal(self.root, self.source, self.release, bootstrap=True)
        self.addCleanup(terminal.close)
        terminal.pump(2.5)
        self.assertIn("download pinned packages", terminal.transcript)
        self.assertFalse((self.root / "data").exists())
        terminal.send(b"2\r", 1.5)
        terminal.send(b"\r", 1.2)
        terminal.send(b"\x1b[B", 0.7)
        terminal.send(b"\r", 1.0)
        terminal.send(b"\r", 1.0)
        terminal.send(b"\r", 1.2)
        terminal.send(b"\r", 1.2)
        terminal.send(b"\r", 1.2)
        terminal.send(b"\r", 1.2)
        terminal.send(b"\r", 1.2)
        terminal.send(b"\r", 2.5)
        import yaml
        settings = yaml.safe_load((self.root / "data/config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertEqual(settings["protocol"], "systemone")

    def test_review_screen_shows_effective_settings(self):
        terminal = self.start()
        terminal.send(b"\r", 1.2)
        terminal.send(b"\r", 1.0)
        terminal.send(b"\r", 1.0)
        terminal.send(b"\r", 1.4)
        terminal.send(b"\r", 1.4)
        terminal.send(b"\r", 1.4)
        terminal.send(b"\r", 1.4)
        terminal.send(b"\r", 2.5)
        self.assertIn("06 / Review", terminal.transcript)
        self.assertIn("Protocol", terminal.transcript)
        self.assertIn("chat_completions", terminal.transcript)
        self.assertIn("Credential", terminal.transcript)

    def test_ctrl_c_writes_nothing(self):
        terminal = self.start()
        terminal.send(b"\x03", 2.0)
        self.assertFalse((self.root / "data").exists())

    def test_eof_writes_nothing(self):
        terminal = self.start()
        terminal.send(b"\x04", 2.0)
        self.assertFalse((self.root / "data").exists())

    def test_plain_mode_renders_numbered_choices(self):
        environment = dict(os.environ)
        environment.update({"HOME": str(self.root), "HERMES_HOME": str(self.root / "data"),
                            "HERMES_DIR": "", "PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin",
                            "TERM": "xterm-256color", "NO_COLOR": "1"})
        script = self.root / "plain.py"
        script.write_text(
            "import importlib.util, sys, pathlib\n"
            f"spec = importlib.util.spec_from_file_location('i', {str(ROOT / 'scripts/install.py')!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"m.RELEASE = pathlib.Path({str(self.release)!r})\n"
            f"sys.exit(m.main(['--hermes-dir', {str(self.source)!r}, '--plain']))\n")
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.root)
            os.execve(sys.executable, [sys.executable, str(script)], environment)
        transcript = ""
        end = time.time() + 2.5
        while time.time() < end:
            readable, _, _ = select.select([fd], [], [], 0.1)
            if readable:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                transcript += ANSI.sub("", data.decode(errors="replace")).replace("\r", "")
        os.write(fd, b"\r")
        time.sleep(0.4)
        end = time.time() + 2.5
        while time.time() < end:
            readable, _, _ = select.select([fd], [], [], 0.1)
            if readable:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                transcript += ANSI.sub("", data.decode(errors="replace")).replace("\r", "")
        os.close(fd)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        self.assertIn("Plain setup", transcript)
        self.assertIn("Choice [1]", transcript)
        self.assertNotIn("↑/↓", transcript)


if __name__ == "__main__":
    unittest.main()
