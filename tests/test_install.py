import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("one_guard_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_first_install_native_lan_requires_explicit_opt_in(self):
        flags = ("--protocol", "systemone", "--endpoint", "http://192.0.2.1:9292/v1/systemone",
                 "--model", "h2o-lightning-4b", "--response-mode", "probability")
        code, output = self.run_install(*flags)
        self.assertEqual(code, 1, output)
        self.assertIn("--allow-insecure-http", output)
        self.assertFalse(self.home.exists())
        code, output = self.run_install(*flags, "--allow-insecure-http")
        self.assertEqual(code, 0, output)
        settings = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertEqual(settings["protocol"], "systemone")
        self.assertEqual(settings["judge_model"], "h2o-lightning-4b")
        self.assertTrue(settings["allow_insecure_http"])
        self.assertEqual(self.run_install()[0], 0)

    def test_protocol_path_mismatch_refuses_before_writes(self):
        for flags in [("--endpoint", "http://127.0.0.1:8080/v1/systemone"),
                      ("--protocol", "systemone", "--endpoint", "http://127.0.0.1:8080/v1/chat/completions")]:
            with self.subTest(flags=flags):
                self.assertEqual(self.run_install(*flags)[0], 1)
                self.assertFalse(self.home.exists())
        self.assertEqual(self.run_install("--protocol", "systemone")[0], 0)
        settings = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertEqual(settings["endpoint"], "http://127.0.0.1:8080/v1/systemone")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
        self.home = self.root / "data"
        self.source = self.make_source("source")
        self.release = self.root / "release"
        self.release.mkdir()
        (self.release / "plugin.yaml").write_text("name: one-guard\n")
        (self.release / "__init__.py").write_text("VERSION = 1\n")
        self.addCleanup(patch.stopall)
        patch.object(installer, "RELEASE", self.release).start()
        patch.dict(os.environ, {"HOME": str(self.root), "PATH": "", "HERMES_DIR": "", "HERMES_HOME": str(self.home)}, clear=True).start()

    def make_source(self, name):
        source = self.root / name
        (source / "hermes_cli").mkdir(parents=True)
        (source / "hermes_cli/__init__.py").write_text("")
        (source / "hermes").write_text("")
        return source

    def run_install(self, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = installer.main(["--hermes-dir", str(self.source), "--yes", *extra])
        return code, output.getvalue()

    def config(self, text):
        self.home.mkdir(exist_ok=True)
        (self.home / "config.yaml").write_text(text)

    def test_timeout_flag_preservation_and_bounds(self):
        for value in ("121", "nan", "inf", "0"):
            code, output = self.run_install("--timeout", value)
            self.assertEqual(code, 1, output)
            self.assertFalse(self.home.exists())
        code, output = self.run_install("--timeout", "120")
        self.assertEqual(code, 0, output)
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        settings = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertEqual(settings["timeout"], 120)
        code, output = self.run_install("--timeout", "60.5")
        self.assertEqual(code, 0, output)
        settings = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertEqual(settings["timeout"], 60.5)

    def test_preserved_timeout_121_refuses_before_writes(self):
        text = "plugins: {entries: {one-guard: {settings: {timeout: 121}}}}\n"
        self.config(text)
        code, output = self.run_install()
        self.assertEqual(code, 1, output)
        self.assertEqual((self.home / "config.yaml").read_text(), text)
        self.assertFalse((self.home / "plugins").exists())

    def test_host_hook_budget_warning_without_global_changes(self):
        for budget, timeout, flags, warning in (
                (None, 60, (), True),
                (None, 5, (), False),
                (30, 60, ("--timeout", "60"), True),
                (65, 60, (), False),
                (0, 120, (), False),
                (10, 20, (), True)):
            with self.subTest(budget=budget, timeout=timeout, flags=flags):
                plugins = {"enabled": ["one-guard"],
                           "entries": {"one-guard": {"settings": {"timeout": timeout}}}}
                if budget is not None:
                    plugins["hook_callback_timeout"] = budget
                self.config(yaml.safe_dump({"plugins": plugins}))
                code, output = self.run_install(*flags)
                self.assertEqual(code, 0, output)
                self.assertEqual("Warning:" in output, warning, output)
                if warning:
                    self.assertIn("bounded by the Hermes host hook timeout", output)
                    self.assertIn(f"plugins.hook_callback_timeout >= {timeout + 5}", output)
                    self.assertEqual(output.count("Warning:"), 1)
                installed = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]
                if budget is None:
                    self.assertNotIn("hook_callback_timeout", installed)
                else:
                    self.assertEqual(installed["hook_callback_timeout"], budget)

    def test_absent_timeout_keeps_runtime_default(self):
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        self.assertNotIn("Warning:", output)
        settings = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertNotIn("timeout", settings)

    def test_install_preserves_config_and_secures_files(self):
        self.config("model: {name: existing}\nextra: [null, true, 2]\nplugins:\n  enabled: [other]\n  disabled: [one-guard, other-disabled]\n  entries:\n    other: {settings: {value: 9}}\n    one-guard:\n      settings: {threshold: 0.2}\n")
        before = (self.home / "config.yaml").read_bytes()
        with patch.dict(os.environ, {"ONE_GUARD_TOKEN": "secret-token"}):
            code, output = self.run_install("--model", "judge", "--response-mode", "probability")
        self.assertEqual(code, 0, output)
        self.assertNotIn("secret-token", output)
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        self.assertEqual(config["model"], {"name": "existing"})
        self.assertEqual(config["extra"], [None, True, 2])
        self.assertEqual(config["plugins"]["enabled"], ["other", "one-guard"])
        self.assertEqual(config["plugins"]["disabled"], ["other-disabled"])
        self.assertEqual(config["plugins"]["entries"]["other"], {"settings": {"value": 9}})
        self.assertEqual(config["plugins"]["entries"]["one-guard"]["settings"]["threshold"], 0.2)
        self.assertNotIn("token", config["plugins"]["entries"]["one-guard"]["settings"])
        self.assertEqual(dotenv_values(self.home / ".env")["ONE_GUARD_TOKEN"], "secret-token")
        self.assertEqual(config["plugins"]["entries"]["one-guard"]["settings"]["mode"], "probability")
        self.assertEqual(config["plugins"]["entries"]["one-guard"]["settings"]["judge_model"], "judge")
        self.assertNotIn("model", config["plugins"]["entries"]["one-guard"]["settings"])
        backup = next(self.home.glob(".one-guard-backup-*"))
        self.assertEqual((backup / "config.yaml").read_bytes(), before)
        self.assertEqual(stat.S_IMODE((self.home / "config.yaml").stat().st_mode), 0o600)
        for path in (self.home / "plugins/one-guard").rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        self.assertFalse((self.source / "plugins").exists())

    def test_rejects_malformed_duplicate_and_wrong_schema_without_replacing_config(self):
        for text in ("broken: [", "x: 1\nx: 2", "[]", "null", "!!map [1, 2]", "plugins: []", "plugins: {enabled: text}",
                     "plugins: {entries: {one-guard: {settings: []}}}", "plugins: {disabled: [1]}",
                     "plugins: {enabled: [a, a]}",
                     "plugins: {entries: {one-guard: {settings: {threshold: true}}}}",
                     "plugins: {entries: {one-guard: {settings: {timeout: .nan}}}}",
                     "plugins: {entries: {one-guard: {settings: {mode: wrong}}}}"):
            with self.subTest(text=text):
                self.config(text)
                code, output = self.run_install()
                self.assertEqual(code, 1, output)
                self.assertEqual((self.home / "config.yaml").read_text(), text)
                self.assertFalse((self.home / "plugins/one-guard").exists())

    def test_yaml_merges_and_aliases_preserve_unrelated_semantics(self):
        text = "defaults: &base {enabled: [other]}\nplugins: *base\nshared: &shared {x: 1, y: 2}\nmerged: {<<: *shared, x: 9}\n"
        self.config(text)
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        self.assertEqual(config["defaults"], {"enabled": ["other"]})
        self.assertEqual(config["shared"], {"x": 1, "y": 2})
        self.assertEqual(config["merged"], {"x": 9, "y": 2})
        self.assertEqual(config["plugins"]["enabled"], ["other", "one-guard"])

    def test_duplicate_key_inside_merge_rejected(self):
        self.config("x: &x {a: 1, a: 2}\ny: {<<: *x}\n")
        self.assertEqual(self.run_install()[0], 1)

    def test_safe_update_and_backups(self):
        self.assertEqual(self.run_install()[0], 0)
        (self.release / "__init__.py").write_text("VERSION = 2\n")
        self.assertEqual(self.run_install()[0], 0)
        target = self.home / "plugins/one-guard"
        self.assertEqual((target / "__init__.py").read_text(), "VERSION = 2\n")
        old = [p / "one-guard/__init__.py" for p in self.home.glob(".one-guard-backup-*")]
        self.assertTrue(any(p.exists() and p.read_text() == "VERSION = 1\n" for p in old))
        self.assertEqual(yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["enabled"], ["one-guard"])

    def test_modified_and_alien_directories_rejected(self):
        target = self.home / "plugins/one-guard"
        target.mkdir(parents=True)
        alien = target / "important.txt"
        alien.write_text("keep me")
        self.assertEqual(self.run_install()[0], 1)
        self.assertEqual(alien.read_text(), "keep me")
        alien.unlink()
        target.rmdir()
        self.assertEqual(self.run_install()[0], 0)
        (target / "__init__.py").write_text("user edit")
        self.assertEqual(self.run_install()[0], 1)
        self.assertEqual((target / "__init__.py").read_text(), "user edit")

    def test_unknown_file_in_owned_directory_rejected(self):
        self.assertEqual(self.run_install()[0], 0)
        target = self.home / "plugins/one-guard"
        (target / "user-data").write_text("keep")
        self.assertEqual(self.run_install()[0], 1)
        self.assertEqual((target / "user-data").read_text(), "keep")

    def test_symlink_inside_owned_plugin_rejected(self):
        self.assertEqual(self.run_install()[0], 0)
        outside = self.root / "important.py"
        outside.write_text("untouched")
        path = self.home / "plugins/one-guard/__init__.py"
        path.unlink()
        path.symlink_to(outside)
        self.assertEqual(self.run_install()[0], 1)
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_text(), "untouched")

    def test_symlinks_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        for relative in ("", "config.yaml", "plugins", "plugins/one-guard", ".one-guard-install.lock"):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory(dir=self.root) as work:
                    home = Path(work) / "data"
                    path = home / relative if relative else home
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.symlink_to(outside)
                    code, output = self.run_install("--hermes-home", str(home))
                    self.assertEqual(code, 1, output)
                    self.assertTrue(path.is_symlink())
        (self.release / "unsafe").symlink_to(outside)
        self.assertEqual(self.run_install()[0], 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_hardlinked_config_rejected(self):
        original = self.root / "original"
        original.write_text("model: keep\n")
        self.home.mkdir()
        os.link(original, self.home / "config.yaml")
        self.assertEqual(self.run_install()[0], 1)
        self.assertEqual(original.read_text(), "model: keep\n")

    def test_rolls_back_plugin_when_config_replace_fails(self):
        self.assertEqual(self.run_install()[0], 0)
        before = (self.home / "config.yaml").read_bytes()
        target = self.home / "plugins/one-guard/__init__.py"
        (self.release / "__init__.py").write_text("VERSION = 2\n")
        replace = os.replace

        def fail_config(source, destination):
            if Path(source).name == "new-config.yaml":
                raise OSError("simulated config write failure")
            return replace(source, destination)

        with patch.object(installer.os, "replace", side_effect=fail_config):
            code, output = self.run_install()
        self.assertEqual(code, 1, output)
        self.assertEqual(target.read_text(), "VERSION = 1\n")
        self.assertEqual((self.home / "config.yaml").read_bytes(), before)

    def test_discovery_env_path_common_and_selector(self):
        common = self.make_source(".hermes/hermes-agent")
        with patch.dict(os.environ, {"HERMES_DIR": str(self.source)}):
            self.assertEqual(installer.discover_sources(), [self.source, common])
            with patch.object(installer.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="2"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(installer.select_source(None), common)
            with patch.object(installer.sys.stdin, "isatty", return_value=False), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(installer.InstallError):
                    installer.select_source(None)
        with patch.object(installer.shutil, "which", return_value=str(self.source / "hermes")):
            self.assertEqual(installer.discover_sources(), [self.source, common])

    def test_no_discovery_prompts_or_fails_without_stdin(self):
        with patch.object(installer.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(installer.InstallError, "No interactive stdin"):
                installer.select_source(None)
        with patch.object(installer.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value=str(self.source)):
            self.assertEqual(installer.select_source(None), self.source)

    def test_no_stdin_requires_yes(self):
        with patch.object(installer.sys.stdin, "isatty", return_value=False), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.main(["--hermes-dir", str(self.source)]), 1)
        self.assertFalse(self.home.exists())

    def test_interactive_setup_requires_a_terminal(self):
        with patch.object(installer.sys.stdin, "isatty", return_value=False), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.main(["--hermes-dir", str(self.source)]), 1)
        self.assertFalse(self.home.exists())

    def test_interactive_cancellation_writes_nothing(self):
        output = io.StringIO()
        with patch.object(installer.sys.stdin, "isatty", return_value=True), patch.object(output, "isatty", return_value=True), patch("builtins.input", side_effect=["", "1", "1", "", "", "1", "", "1", "3"]), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(installer.main(["--hermes-dir", str(self.source), "--plain"]), 1)
        self.assertIn("06 / Review", output.getvalue())
        self.assertFalse(self.home.exists())

    def test_missing_yaml_actionable_and_no_pip(self):
        original_import = __import__

        def without_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_yaml), patch.object(installer.subprocess, "run") as run:
            with self.assertRaisesRegex(installer.InstallError, "PyYAML is required"):
                installer.load_yaml_module(self.source, [])
            run.assert_not_called()

    def test_yaml_reexec_uses_hermes_python(self):
        python = self.source / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        original_import = __import__

        def without_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_yaml), patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), patch.object(installer.os, "execve", side_effect=RuntimeError("reexec")) as execute:
            with self.assertRaisesRegex(RuntimeError, "reexec"):
                installer.load_yaml_module(self.source, ["--yes"])
        self.assertEqual(execute.call_args.args[0], str(python))
        self.assertEqual(execute.call_args.args[2]["ONE_GUARD_INSTALL_REEXEC"], "1")

    def test_invalid_endpoint_and_no_token_cli(self):
        for endpoint in ("http://remote.test/v1/chat/completions", "https://user:secret@host/v1", "file:///tmp/a"):
            self.assertEqual(self.run_install("--endpoint", endpoint)[0], 1)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            installer.parser().parse_args(["--token", "secret"])

    def test_token_invalid_characters_not_echoed_and_existing_token_preserved(self):
        for token in ("bad\nsecret", "non-ascii-\u00e9", "${INTERPOLATED}"):
            with patch.dict(os.environ, {"ONE_GUARD_TOKEN": token}):
                code, output = self.run_install()
            self.assertEqual(code, 1, output)
            self.assertNotIn(token, output)
        self.config("plugins: {entries: {one-guard: {settings: {token: existing-secret}}}}\n")
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        settings = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertNotIn("token", settings)
        self.assertEqual(dotenv_values(self.home / ".env")["ONE_GUARD_TOKEN"], "existing-secret")

    def test_legacy_plugin_migration_matches_upstream_names_and_disabled(self):
        self.config("_config_version: 20\nplugins: {disabled: ['off']}\n")
        for directory, manifest, name in (("alias", "plugin.yaml", "keep"),
                                           ("off-dir", "plugin.yml", "off"),
                                           ("fallback", "plugin.yaml", None)):
            path = self.home / "plugins" / directory
            path.mkdir(parents=True)
            (path / manifest).write_text(yaml.safe_dump({"name": name}))
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        self.assertEqual(config["plugins"]["enabled"], ["keep", "fallback", "one-guard"])
        self.assertEqual(config["plugins"]["disabled"], ["off"])

    def test_explicit_empty_enablement_does_not_grandfather(self):
        self.config("plugins: {enabled: []}\n")
        path = self.home / "plugins" / "other"
        path.mkdir(parents=True)
        (path / "plugin.yaml").write_text("name: other\n")
        self.assertEqual(self.run_install()[0], 0)
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        self.assertEqual(config["plugins"]["enabled"], ["one-guard"])

    def test_modern_absent_enablement_preserves_opt_in_and_unversioned_refuses(self):
        self.config("_config_version: 21\nplugins: {}\n")
        path = self.home / "plugins" / "other"
        path.mkdir(parents=True)
        (path / "plugin.yaml").write_text("name: other\n")
        self.assertEqual(self.run_install()[0], 0)
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        self.assertEqual(config["plugins"]["enabled"], ["one-guard"])
        self.config("plugins: {}\n")
        before = (self.home / "config.yaml").read_bytes()
        code, output = self.run_install()
        self.assertEqual(code, 1, output)
        self.assertIn("plugins.enabled", output)
        self.assertEqual((self.home / "config.yaml").read_bytes(), before)

    def test_ambiguous_legacy_manifest_refused_before_replacements(self):
        self.config("_config_version: 20\nplugins: {}\n")
        path = self.home / "plugins" / "other"
        path.mkdir(parents=True)
        (path / "plugin.yaml").write_text("name: [bad]\n")
        before = (self.home / "config.yaml").read_bytes()
        code, output = self.run_install()
        self.assertEqual(code, 1, output)
        self.assertIn("plugins.enabled", output)
        self.assertEqual((self.home / "config.yaml").read_bytes(), before)
        self.assertFalse((self.home / ".env").exists())
        self.assertFalse(list(self.home.glob(".one-guard-backup-*")))
        self.assertFalse((self.home / ".one-guard-install.lock").exists())

    def test_secret_preserves_unrelated_env_and_existing_directories(self):
        self.config("plugins: {entries: {one-guard: {settings: {token: old}}}}\n")
        self.home.chmod(0o755)
        original = b'# retained\nOTHER="value # unchanged"\nMULTI=\'a\nb\'\nONE_GUARD_TOKEN="preferred"\n'
        (self.home / ".env").write_bytes(original)
        code, output = self.run_install()
        self.assertEqual(code, 0, output)
        self.assertEqual((self.home / ".env").read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.home / ".env").stat().st_mode), 0o600)
        backup = next(self.home.glob(".one-guard-backup-*"))
        self.assertEqual((backup / ".env").read_bytes(), original)
        with patch.dict(os.environ, {"ONE_GUARD_TOKEN": 'space \\ quote " apostrophe \' #'}):
            code, output = self.run_install()
            self.assertEqual(code, 0, output)
            self.assertEqual(dotenv_values(self.home / ".env", interpolate=False)["ONE_GUARD_TOKEN"], os.environ["ONE_GUARD_TOKEN"])

    def test_secret_unsafe_paths_and_ambiguous_values_refused(self):
        self.config("{}\n")
        path = self.home / ".env"
        outside = self.root / "secret"
        outside.write_text("KEEP=yes\n")
        path.symlink_to(outside)
        self.assertEqual(self.run_install()[0], 1)
        path.unlink()
        os.link(outside, path)
        self.assertEqual(self.run_install()[0], 1)
        path.unlink()
        for raw in ("ONE_GUARD_TOKEN=a\nONE_GUARD_TOKEN=b\n", 'ONE_GUARD_TOKEN="unterminated\n'):
            path.write_text(raw)
            self.assertEqual(self.run_install()[0], 1)
            self.assertEqual(path.read_text(), raw)
        self.assertEqual(outside.read_text(), "KEEP=yes\n")

    def test_secret_rolls_back_with_config_failure(self):
        with patch.dict(os.environ, {"ONE_GUARD_TOKEN": "original"}):
            self.assertEqual(self.run_install()[0], 0)
        before = {name: (self.home / name).read_bytes() for name in ("config.yaml", ".env", "plugins/one-guard/__init__.py")}
        replace = os.replace

        def fail_config(source, destination):
            if Path(source).name == "new-config.yaml":
                raise OSError("simulated failure")
            return replace(source, destination)

        with patch.dict(os.environ, {"ONE_GUARD_TOKEN": "replacement"}), patch.object(installer.os, "replace", side_effect=fail_config):
            self.assertEqual(self.run_install()[0], 1)
        for name, raw in before.items():
            self.assertEqual((self.home / name).read_bytes(), raw)

    def test_first_install_rollback_removes_new_config_and_secret(self):
        replace = os.replace

        def fail_config(source, destination):
            if Path(source).name == "new-config.yaml":
                raise OSError("simulated failure")
            return replace(source, destination)

        with patch.dict(os.environ, {"ONE_GUARD_TOKEN": "new-secret"}), patch.object(installer.os, "replace", side_effect=fail_config):
            code, output = self.run_install()
        self.assertEqual(code, 1, output)
        self.assertNotIn("new-secret", output)
        for name in ("config.yaml", ".env", "plugins/one-guard"):
            self.assertFalse((self.home / name).exists())

    def test_secret_replace_failure_preserves_all_original_files(self):
        self.assertEqual(self.run_install()[0], 0)
        before = {name: (self.home / name).read_bytes() for name in ("config.yaml", ".env", "plugins/one-guard/__init__.py")}
        replace = os.replace

        def fail_env(source, destination):
            if Path(source).name == "new-.env":
                raise OSError("simulated failure")
            return replace(source, destination)

        with patch.dict(os.environ, {"ONE_GUARD_TOKEN": "replacement"}), patch.object(installer.os, "replace", side_effect=fail_env):
            self.assertEqual(self.run_install()[0], 1)
        for name, raw in before.items():
            self.assertEqual((self.home / name).read_bytes(), raw)

    def test_shell_installs_local_release_in_temporary_home(self):
        env = dict(os.environ, PATH=os.path.dirname(sys.executable) + ":/usr/bin:/bin", ONE_GUARD_TOKEN="cli-test-token")
        result = subprocess.run(["/bin/sh", str(ROOT / "install.sh"), "--yes", "--hermes-dir", str(self.source), "--hermes-home", str(self.home), "--model", "test-model", "--response-mode", "probability"], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("cli-test-token", result.stdout + result.stderr)
        for name, data in installer.release_files(ROOT / "plugins/one-guard").items():
            self.assertEqual((self.home / "plugins/one-guard" / name).read_bytes(), data)
        settings = yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]
        self.assertEqual(settings["mode"], "probability")
        self.assertEqual(settings["judge_model"], "test-model")

    def test_shell_help(self):
        result = subprocess.run(["/bin/sh", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True, env={"PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--hermes-home", result.stdout)


if __name__ == "__main__":
    unittest.main()
