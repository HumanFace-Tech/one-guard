import os
import pty
import re
import select
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".test-artifacts"
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


class Terminal:
    def __init__(self, root, source, release, bootstrap=False, fail=False):
        self.root = root
        environment = dict(os.environ)
        environment.update({"HOME": str(root), "HERMES_HOME": str(root / "data"),
                            "HERMES_DIR": "", "PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin",
                            "TERM": "xterm-256color"})
        environment.pop("ONE_GUARD_TOKEN", None)
        if bootstrap:
            environment["HERMES_DIR"] = str(source)
            environment["ONE_GUARD_TOKEN"] = "synthetic-terminal-secret"
        script = root / "run.py"
        stub = ("import contextlib, time\n"
                "available = m.installer_ui.rich_available\n"
                "state = [False]\n"
                "m.installer_ui.rich_available = lambda: state[0] and available()\n"
                "@contextlib.contextmanager\n"
                "def provision():\n"
                "    time.sleep(0.5)\n"
                + ("    raise OSError('offline')\n" if fail else "") +
                "    state[0] = True\n"
                "    yield True\n"
                "m.installer_bootstrap.provision = provision\n") if bootstrap else ""
        script.write_text(
            "import importlib.util, sys, pathlib\n"
            f"spec = importlib.util.spec_from_file_location('i', {str(ROOT / 'scripts/install.py')!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"m.RELEASE = pathlib.Path({str(release)!r})\n"
            + stub
            + ("sys.exit(m.main([]))\n" if bootstrap else
               f"sys.exit(m.main(['--hermes-dir', {str(source)!r}]))\n"))
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

    def exit_code(self):
        pid, status = os.waitpid(self.pid, os.WNOHANG)
        if not pid:
            raise AssertionError("installer parent did not exit")
        return os.waitstatus_to_exitcode(status)


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

    def start_handoff(self):
        import prompt_toolkit
        import wcwidth

        binary = self.source / ".venv/bin/python"
        binary.parent.mkdir(parents=True, exist_ok=True)
        if not binary.exists():
            binary.symlink_to(sys.executable)
        capsule = self.root / "capsule"
        script = self.root / "handoff.py"
        script.write_text(
            "import builtins, contextlib, importlib.util, pathlib, shutil, sys\n"
            f"spec = importlib.util.spec_from_file_location('i', {str(ROOT / 'scripts/install.py')!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "original = builtins.__import__\n"
            "def missing(name, *args, **kwargs):\n"
            "    if name in ('yaml', 'dotenv'): raise ImportError(name)\n"
            "    return original(name, *args, **kwargs)\n"
            "builtins.__import__ = missing\n"
            "available = m.installer_ui.rich_available; state = [False]\n"
            "m.installer_ui.rich_available = lambda: state[0] and available()\n"
            "@contextlib.contextmanager\n"
            "def provision():\n"
            f"    root = pathlib.Path({str(capsule)!r}); root.mkdir()\n"
            "    try:\n"
            f"        shutil.copytree({str(Path(prompt_toolkit.__file__).parent)!r}, root / 'prompt_toolkit')\n"
            f"        shutil.copytree({str(Path(wcwidth.__file__).parent)!r}, root / 'wcwidth')\n"
            "        sys.path.insert(0, str(root)); state[0] = True\n"
            "        yield str(root)\n"
            "    finally:\n"
            "        if str(root) in sys.path: sys.path.remove(str(root))\n"
            "        shutil.rmtree(root)\n"
            "m.installer_bootstrap.provision = provision\n"
            "sys.exit(m.main([]))\n")
        environment = dict(os.environ, HOME=str(self.root), HERMES_HOME=str(self.root / "data"),
                           HERMES_DIR=str(self.source), TERM="xterm-256color")
        environment.pop("NO_COLOR", None)
        environment.pop("ONE_GUARD_INSTALL_REEXEC", None)
        terminal = Terminal.__new__(Terminal)
        terminal.pid, terminal.fd = pty.fork()
        if terminal.pid == 0:
            os.execve(sys.executable, [sys.executable, str(script)], environment)
        terminal.transcript = ""
        self.addCleanup(terminal.close)
        terminal.pump(1)
        terminal.send(b"2\r", 1.5)
        self.assertIn("01 / Hermes source", terminal.transcript)
        self.assertIn("↑/↓", terminal.transcript)
        terminal.send(b"\x1b[B", 0.3)
        terminal.send(b"\x1b[A", 0.3)
        terminal.send(b"\r", 2)
        self.assertIn("02 /", terminal.transcript)
        self.assertTrue(capsule.exists())
        children = Path(f"/proc/{terminal.pid}/task/{terminal.pid}/children").read_text().split()
        self.assertEqual(len(children), 1)
        return terminal, capsule, int(children[0])

    def test_handoff_ctrl_c_reaps_child_and_removes_capsule(self):
        terminal, capsule, child = self.start_handoff()
        terminal.send(b"\x03", 2)
        self.assertEqual(terminal.exit_code(), 1)
        self.assertFalse(Path(f"/proc/{child}").exists())
        self.assertFalse(capsule.exists())
        self.assertFalse((self.root / "data").exists())

    def test_handoff_sigterm_reaps_child_and_removes_capsule(self):
        terminal, capsule, child = self.start_handoff()
        os.kill(terminal.pid, signal.SIGTERM)
        terminal.pump(3)
        self.assertEqual(terminal.exit_code(), 128 + signal.SIGTERM)
        self.assertFalse(Path(f"/proc/{child}").exists())
        self.assertFalse(capsule.exists())
        self.assertFalse((self.root / "data").exists())

    def test_handoff_parent_only_sigint_and_sighup(self):
        for sig in (signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=sig):
                terminal, capsule, child = self.start_handoff()
                os.kill(terminal.pid, sig)
                terminal.pump(3)
                self.assertEqual(terminal.exit_code(), 128 + sig)
                self.assertFalse(Path(f"/proc/{child}").exists())
                self.assertFalse(capsule.exists())

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
        self.assertNotIn("01 / Hermes source", terminal.transcript)
        self.assertFalse((self.root / "data").exists())
        terminal.send(b"2\r", 1.5)
        self.assertIn("Preparing optional terminal UI", terminal.transcript)
        self.assertIn("01 / Hermes source", terminal.transcript)
        self.assertIn("↑/↓", terminal.transcript)
        self.assertNotIn("synthetic-terminal-secret", terminal.transcript)
        terminal.send(b"\x1b[B", 0.3)
        terminal.send(b"\x1b[A", 0.3)
        terminal.send(b"\r", 1.2)
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
        self.assertNotIn("synthetic-terminal-secret", terminal.transcript)

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

    def test_offline_bootstrap_falls_back_before_source_selection(self):
        terminal = Terminal(self.root, self.source, self.release, bootstrap=True, fail=True)
        self.addCleanup(terminal.close)
        terminal.pump(1)
        terminal.send(b"2\r", 1.5)
        self.assertIn("Preparing optional terminal UI", terminal.transcript)
        self.assertIn("Optional UI unavailable", terminal.transcript)
        self.assertIn("01 / Hermes source", terminal.transcript)
        self.assertIn("Choice [1]", terminal.transcript)
        terminal.send(b"\x03", 0.5)
        self.assertFalse((self.root / "data").exists())

    def test_cancel_during_bootstrap_writes_nothing(self):
        terminal = Terminal(self.root, self.source, self.release, bootstrap=True)
        self.addCleanup(terminal.close)
        terminal.pump(1)
        terminal.send(b"2\r", 0.1)
        terminal.send(b"\x03", 0.7)
        self.assertIn("Preparing optional terminal UI", terminal.transcript)
        self.assertFalse((self.root / "data").exists())

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
