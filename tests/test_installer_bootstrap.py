import contextlib
import importlib.util
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer_bootstrap_test", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
bootstrap = installer.installer_bootstrap


class BootstrapTests(unittest.TestCase):
    def test_lock_pins_hashes(self):
        lock = (ROOT / "scripts/installer-ui-requirements.txt").read_text()
        self.assertIn("prompt_toolkit==3.0.53 --hash=sha256:01c0891d7f9237d5e339f7d3e42cdae80b7534abb1c7c0e3352efba6231492f2", lock)
        self.assertIn("wcwidth==0.2.13 --hash=sha256:3da69048e4540d84af32131829ff948f1e022c1c6bdb8d6102117aac784f6859", lock)

    def test_provision_isolated_environment_and_cleanup(self):
        calls = []
        def fake_run(argv, env, timeout, capture=False):
            calls.append((argv, env.copy(), timeout))
            root = Path(env["HOME"])
            if capture:
                path = root / "venv/lib/site-packages"
                path.mkdir(parents=True)
                return str(path)
            return ""
        with patch.object(bootstrap, "run", side_effect=fake_run), patch.dict(os.environ, {"HTTPS_PROXY": "secret", "ONE_GUARD_TOKEN": "secret"}):
            with bootstrap.provision():
                root = Path(calls[0][1]["HOME"])
                self.assertIn(str(root / "venv/lib/site-packages"), sys.path)
                import types
                sys.modules["prompt_toolkit.fake_bootstrap_test"] = types.ModuleType("test")
            self.assertFalse(root.exists())
            self.assertNotIn(str(root / "venv/lib/site-packages"), sys.path)
            self.assertNotIn("prompt_toolkit.fake_bootstrap_test", sys.modules)
        for _, env, timeout in calls:
            self.assertNotIn("HTTPS_PROXY", env)
            self.assertNotIn("ONE_GUARD_TOKEN", env)
            self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
            self.assertLessEqual(timeout, 45)
        command = calls[1][0]
        for flag in ("--require-virtualenv", "--require-hashes", "--no-deps", "--no-cache-dir", "--only-binary=:all:"):
            self.assertIn(flag, command)

    def test_failure_removes_capsule(self):
        roots = []
        def fail(argv, env, timeout, capture=False):
            roots.append(Path(env["HOME"]))
            raise subprocess.TimeoutExpired(argv, timeout)
        with patch.object(bootstrap, "run", side_effect=fail):
            with self.assertRaises(subprocess.TimeoutExpired):
                with bootstrap.provision():
                    self.fail("must not enter")
        self.assertFalse(roots[0].exists())

    def test_capsule_isolates_restores_and_nests_modules(self):
        originals = {name: types.ModuleType(name) for name in
                     ("prompt_toolkit", "prompt_toolkit.old", "wcwidth", "wcwidth.old")}
        unrelated = types.ModuleType("wcwidth_extra")
        def fake_run(argv, env, timeout, capture=False):
            if capture:
                path = Path(env["HOME"]) / "venv/lib/site-packages"
                for package in ("prompt_toolkit", "wcwidth"):
                    directory = path / package
                    directory.mkdir(parents=True)
                    (directory / "__init__.py").write_text("origin = 'capsule'\n")
                    (directory / "part.py").write_text("origin = 'capsule'\n")
                return str(path)
            return ""

        with patch.dict(sys.modules, {**originals, "wcwidth_extra": unrelated}), patch.object(bootstrap, "run", side_effect=fake_run):
            with bootstrap.provision():
                for package in ("prompt_toolkit", "wcwidth"):
                    self.assertEqual(importlib.import_module(package).origin, "capsule")
                    self.assertEqual(importlib.import_module(package + ".part").origin, "capsule")
                    self.assertNotIn(package + ".old", sys.modules)
                outer = {name: module for name, module in sys.modules.copy().items() if bootstrap.ui_module(name)}
                with self.assertRaisesRegex(RuntimeError, "inner"):
                    with bootstrap.provision():
                        self.assertIsNot(importlib.import_module("wcwidth"), outer["wcwidth"])
                        importlib.import_module("prompt_toolkit.part")
                        raise RuntimeError("inner")
                self.assertEqual({name: module for name, module in sys.modules.copy().items() if bootstrap.ui_module(name)}, outer)
            for name, module in originals.items():
                self.assertIs(sys.modules[name], module)
            self.assertNotIn("prompt_toolkit.part", sys.modules)
            self.assertNotIn("wcwidth.part", sys.modules)
            self.assertIs(sys.modules["wcwidth_extra"], unrelated)

    def test_failed_import_restores_preloaded_modules(self):
        original = types.ModuleType("wcwidth")
        def fake_run(argv, env, timeout, capture=False):
            if capture:
                path = Path(env["HOME"]) / "venv/lib/site-packages"
                directory = path / "prompt_toolkit"
                directory.mkdir(parents=True)
                (directory / "__init__.py").write_text("from . import part\nraise ImportError('broken capsule')\n")
                (directory / "part.py").write_text("value = 1\n")
                return str(path)
            return ""
        with patch.dict(sys.modules, {"wcwidth": original}), patch.object(bootstrap, "run", side_effect=fake_run):
            before = {name: module for name, module in sys.modules.copy().items() if bootstrap.ui_module(name)}
            with self.assertRaisesRegex(ImportError, "broken capsule"):
                with bootstrap.provision():
                    importlib.import_module("prompt_toolkit")
            self.assertEqual({name: module for name, module in sys.modules.copy().items() if bootstrap.ui_module(name)}, before)

    def test_rejects_purelib_outside_capsule(self):
        with patch.object(bootstrap, "run", side_effect=["", "", tempfile.gettempdir()]):
            with self.assertRaises(ValueError):
                with bootstrap.provision():
                    self.fail("must not enter")

    def test_yes_never_provisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "hermes_cli").mkdir(parents=True)
            (source / "hermes_cli/__init__.py").touch()
            (source / "hermes").touch()
            with patch.object(installer.installer_bootstrap, "provision") as provision, patch.object(installer, "release_files", side_effect=installer.InstallError("stop")), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(installer.main(["--yes", "--hermes-dir", str(source), "--hermes-home", str(root / "home")]), 1)
            provision.assert_not_called()
            self.assertFalse((root / "home").exists())

    def test_eligibility_independent_of_import(self):
        args = installer.parser().parse_args([])
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stdout, "isatty", return_value=True), patch.dict(os.environ, {"TERM": "xterm"}, clear=True), patch.object(installer.installer_ui, "rich_available", return_value=False):
            self.assertTrue(installer.installer_ui.wants_rich(args))
            args.plain = True
            self.assertFalse(installer.installer_ui.wants_rich(args))

    def test_hermes_interpreter_handoff_keeps_bootstrap_owned_by_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            python = source / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            original_import = __import__
            def missing(name, *args, **kwargs):
                if name == "yaml":
                    raise ImportError(name)
                return original_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=missing), patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, patch.object(installer, "managed_handoff", return_value=0) as handoff, patch.object(installer.os, "execve") as execute, patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(installer.InterpreterResult) as result:
                    installer.load_yaml_module(source, [], ui_path="/temporary/ui")
            self.assertEqual(result.exception.args[0], 0)
            self.assertEqual(run.call_count, 1)
            self.assertIn("/temporary/ui", handoff.call_args.args[0])
            self.assertEqual(handoff.call_args.args[1]["ONE_GUARD_INSTALL_REEXEC"], "1")
            execute.assert_not_called()

    def test_missing_required_dependencies_prevents_provision(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            real_import = __import__
            def missing(name, *args, **kwargs):
                if name in ("yaml", "dotenv"):
                    raise ImportError(name)
                return real_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=missing), patch.object(bootstrap, "provision") as provision:
                with self.assertRaises(installer.InstallError):
                    installer.load_yaml_module(source, [], prefer_ui=True)
                provision.assert_not_called()

    def test_timeout_kills_process_group(self):
        process = Mock(pid=12345, returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired(["python"], 1), (b"", None)]
        with patch.object(bootstrap.subprocess, "Popen", return_value=process), patch.object(bootstrap.os, "killpg") as kill, patch.object(bootstrap.time, "monotonic", side_effect=[0, 0, 1]):
            with self.assertRaises(subprocess.TimeoutExpired):
                bootstrap.run(["python"], {}, 1)
        kill.assert_called_once_with(12345, bootstrap.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_decline_or_cancel_never_provisions_or_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "hermes_cli").mkdir(parents=True)
            (source / "hermes_cli/__init__.py").touch()
            (source / "hermes").touch()
            for cancel in (False, True):
                ui = Mock()
                ui.choose.side_effect = KeyboardInterrupt() if cancel else None
                ui.choose.return_value = False
                with patch.object(installer.installer_ui, "create_ui", return_value=ui), patch.object(installer.installer_ui, "wants_rich", return_value=True), patch.object(installer.installer_ui, "rich_available", return_value=False), patch.object(installer, "load_yaml_module", return_value=object()), patch.object(installer, "run_wizard", side_effect=KeyboardInterrupt()) as wizard, patch.object(bootstrap, "provision") as provision, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(installer.main(["--hermes-dir", str(source), "--hermes-home", str(root / "home")]), 1)
                provision.assert_not_called()
                self.assertEqual(wizard.called, not cancel)
                self.assertFalse((root / "home").exists())


if __name__ == "__main__":
    unittest.main()
