import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".test-artifacts"
SPEC = importlib.util.spec_from_file_location("one_guard_installer_ui", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class ScriptedUI:
    def __init__(self, *, choices=None, texts=None):
        self.secrets = []
        self.choices = list(choices or [])
        self.texts = {label: (list(values) if isinstance(values, list) else [values])
                      for label, values in (texts or {}).items()}
        self.asked = []
        self.said = []

    def clean(self, value):
        text = str(value)
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[credential]")
        return text

    def say(self, message=""):
        self.said.append(str(message))

    def header(self, title, description=""):
        self.said.append(title)

    def summary(self, rows):
        for label, value in rows:
            self.said.append(f"{label}: {value}")

    def choose(self, title, choices, default=0):
        self.asked.append(("choose", title))
        if not self.choices:
            return choices[default][0]
        choice = self.choices.pop(0)
        if choice not in [value for value, _ in choices]:
            raise AssertionError(f"Invalid scripted choice {choice!r} for {title}")
        return choice

    def text(self, label, default="", validate=str, secret=False, allow_empty=False):
        self.asked.append(("text", label))
        queue = self.texts.get(label)
        if queue:
            value = queue.pop(0) if len(queue) > 1 else queue[0]
        else:
            value = default
        if secret and value:
            self.secrets.append(value)
        if not value and allow_empty:
            return None
        return validate(value)


class WizardTests(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="wizard-", dir=ARTIFACTS)
        self.addCleanup(lambda: self.assertFalse(Path(self.temp.name).exists()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assertEqual(self.root.parent, ARTIFACTS)
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
        self.home = self.root / "data"
        self.source = self.root / "source"
        (self.source / "hermes_cli").mkdir(parents=True)
        (self.source / "hermes_cli/__init__.py").write_text("")
        (self.source / "hermes").write_text("")
        self.release = self.root / "release"
        self.release.mkdir()
        (self.release / "plugin.yaml").write_text("name: one-guard\n")
        (self.release / "__init__.py").write_text("VERSION = 1\n")
        self.addCleanup(patch.stopall)
        patch.object(installer, "RELEASE", self.release).start()
        patch.dict(os.environ, {"HOME": str(self.root), "PATH": "", "HERMES_DIR": "",
                                "HERMES_HOME": str(self.home)}, clear=True).start()

    def run_wizard(self, ui, extra=()):
        with patch.object(installer.installer_ui, "create_ui", return_value=ui):
            with patch("builtins.print"):
                code = installer.main(["--hermes-dir", str(self.source), "--plain", *extra])
        return code, "\n".join(ui.said)

    def settings(self):
        import yaml
        return yaml.safe_load((self.home / "config.yaml").read_text())["plugins"]["entries"]["one-guard"]["settings"]

    def test_full_wizard_installs_with_defaults(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertTrue((self.home / "plugins/one-guard/__init__.py").is_file())
        settings = self.settings()
        self.assertEqual(settings["protocol"], "chat_completions")
        self.assertEqual(settings["mode"], "yes_no")
        self.assertEqual(settings["endpoint"], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertEqual(settings["timeout"], 5)

    def test_wizard_systemone_lan_endpoint_is_accepted(self):
        ui = ScriptedUI(choices=["systemone", True, "probability", "keep", "install"],
                        texts={"Judge endpoint": "http://192.0.2.1:9292/v1/systemone",
                               "Judge model": "h2o-lightning-4b",
                               "Timeout in seconds": "60",
                               "Risk threshold": "0.5"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        settings = self.settings()
        self.assertEqual(settings["protocol"], "systemone")
        self.assertEqual(settings["endpoint"], "http://192.0.2.1:9292/v1/systemone")
        self.assertEqual(settings["judge_model"], "h2o-lightning-4b")
        self.assertEqual(settings["timeout"], 60)
        self.assertTrue(settings["allow_insecure_http"])

    def test_wizard_cancel_writes_nothing(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", "cancel"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 1, said)
        self.assertFalse(self.home.exists())

    def test_wizard_edit_then_install(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", "edit", "judge",
                                 "systemone", False, "probability", "install"],
                        texts={"Judge endpoint": ["http://127.0.0.1:8080/v1/chat/completions",
                                                 "http://127.0.0.1:8080/v1/systemone"],
                               "Judge model": "m",
                               "Timeout in seconds": "10",
                               "Risk threshold": "0.25"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        settings = self.settings()
        self.assertEqual(settings["protocol"], "systemone")
        self.assertEqual(settings["threshold"], 0.25)

    def test_invalid_endpoint_is_rejected_before_install(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", "install"],
                        texts={"Judge endpoint": "http://evil.test/v1/wrong"})
        with patch.object(installer.Path, "mkdir") as mkdir, patch.object(installer, "apply_install") as apply:
            code, said = self.run_wizard(ui)
        mkdir.assert_not_called()
        apply.assert_not_called()
        self.assertEqual(code, 1, said)
        self.assertFalse(self.home.exists())

    def test_relative_home_is_confined_to_temporary_workspace(self):
        relative_home = f"wizard-relative-home-{self.root.name}"
        workspace_target = ROOT / relative_home
        self.assertFalse(workspace_target.exists())
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", "install"],
                        texts={"Hermes data home": relative_home})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertTrue((self.root / relative_home / "config.yaml").is_file())
        self.assertFalse(workspace_target.exists())

    def test_credential_is_masked_in_review_and_never_written_to_yaml(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "replace", "install"],
                        texts={"New bearer credential  (blank removes it)": "test-credential"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertNotIn("test-credential", said)
        from dotenv import dotenv_values
        self.assertEqual(dotenv_values(self.home / ".env")["ONE_GUARD_TOKEN"], "test-credential")
        self.assertNotIn("test-credential", (self.home / "config.yaml").read_text())

    def test_plain_ui_rejects_invalid_choice_then_continues(self):
        ui = installer.installer_ui.PlainUI()
        prompts = io.StringIO("9\n2\n")
        import builtins
        original = builtins.input
        builtins.input = lambda text="": prompts.readline().rstrip("\n")
        try:
            self.assertEqual(ui.choose("Pick", [("a", "A"), ("b", "B")], 0), "b")
        finally:
            builtins.input = original

    def test_safe_text_escapes_control_characters(self):
        rendered = installer.installer_ui.safe_text("bad\x1b[2Jvalue")
        self.assertNotIn("\x1b", rendered)
        self.assertIn("\\u001b", rendered)

    def test_wizard_requires_a_terminal(self):
        import contextlib
        output = io.StringIO()
        with patch.object(installer.sys.stdin, "isatty", return_value=False):
            with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
                code = installer.main(["--hermes-dir", str(self.source)])
        self.assertEqual(code, 1)
        self.assertIn("terminal", output.getvalue())

    def test_eof_inside_a_menu_exits_cleanly_without_writing(self):
        class EofUI(ScriptedUI):
            def choose(self, title, choices, default=0):
                raise EOFError()

        code, said = self.run_wizard(EofUI())
        self.assertEqual(code, 1)
        self.assertFalse(self.home.exists())

    def test_eof_inside_a_text_field_exits_cleanly_without_writing(self):
        class EofUI(ScriptedUI):
            def text(self, label, default="", validate=str, secret=False, allow_empty=False):
                raise EOFError()

        code, said = self.run_wizard(EofUI())
        self.assertEqual(code, 1)
        self.assertFalse(self.home.exists())

    def test_current_settings_accepts_a_string_home(self):
        import yaml
        settings = installer.current_settings(str(self.home), yaml)
        self.assertEqual(settings, {})

    def test_plain_ui_treats_eof_as_interruption(self):
        ui = installer.installer_ui.PlainUI()
        import builtins
        original = builtins.input
        builtins.input = lambda text="": (_ for _ in ()).throw(EOFError())
        try:
            with self.assertRaises(KeyboardInterrupt):
                ui.choose("Pick", [("a", "A")], 0)
        finally:
            builtins.input = original

    def test_untrusted_values_cannot_emit_terminal_escapes(self):
        hostile = "\x1b[2J\x1b[31mPWNED\x07"
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", "install"],
                        texts={"Judge model": hostile})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 1)
        self.assertNotIn("\x1b", said)
        self.assertNotIn("\x07", said)
        self.assertFalse(self.home.exists())

    def test_toolkit_ui_escapes_control_characters(self):
        ui = installer.installer_ui.ToolkitUI()
        rendered = ui.clean("\x1b[2Jvalue\x07")
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)

    def test_credential_never_reaches_the_terminal(self):
        secret = "TOP-SECRET-TOKEN-XYZ"

        class SecretUI(ScriptedUI):
            def text(self, label, default="", validate=str, secret=False, allow_empty=False):
                if secret:
                    self.secrets.append(secret_value)
                    return secret_value
                return ScriptedUI.text(self, label, default, validate, secret, allow_empty)

        secret_value = "TOP-SECRET-TOKEN-XYZ"
        ui = SecretUI(choices=["chat_completions", False, "yes_no", "replace", "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertNotIn(secret, said)
        self.assertNotIn(secret, (self.home / "config.yaml").read_text())
        from dotenv import dotenv_values
        self.assertEqual(dotenv_values(self.home / ".env")["ONE_GUARD_TOKEN"], secret)

    def test_cancelling_at_the_review_writes_nothing(self):
        temp = tempfile.TemporaryDirectory(prefix="cancel-", dir=self.root)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        home = root / "data"
        source = root / "source"
        (source / "hermes_cli").mkdir(parents=True)
        (source / "hermes_cli/__init__.py").write_text("")
        (source / "hermes").write_text("")
        release = root / "release"
        release.mkdir()
        (release / "plugin.yaml").write_text("name: one-guard\n")
        (release / "__init__.py").write_text("VERSION = 1\n")

        class CancelUI(ScriptedUI):
            def choose(self, title, choices, default=0):
                options = [value for value, _ in choices]
                if "cancel" in options:
                    return "cancel"
                return choices[default][0]

        with patch.object(installer, "RELEASE", release), patch.dict(
                os.environ, {"HOME": str(root), "PATH": "", "HERMES_DIR": "",
                             "HERMES_HOME": str(home)}, clear=True):
            with patch.object(installer.installer_ui, "create_ui", return_value=CancelUI()):
                with patch("builtins.print"):
                    code = installer.main(["--hermes-dir", str(source), "--plain"])
        self.assertEqual(code, 1)
        self.assertFalse(home.exists())

    def test_wizard_cannot_bypass_the_shared_validation(self):
        with self.assertRaises(installer.InstallError):
            installer.merge_config({}, installer.trial_args(
                protocol="systemone", endpoint="http://127.0.0.1:8080/v1/chat/completions",
                insecure_override=False))
        with self.assertRaises(installer.InstallError):
            installer.merge_config({}, installer.trial_args(
                protocol="systemone", endpoint="http://192.0.2.1:9292/v1/systemone",
                insecure_override=False))

    def test_clearing_the_credential_removes_it_from_the_env_file(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / ".env").write_text('OTHER="keep"\nONE_GUARD_TOKEN="old-secret"\n')
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "clear", "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        from dotenv import dotenv_values
        values = dotenv_values(self.home / ".env")
        self.assertIsNone(values.get("ONE_GUARD_TOKEN"))
        self.assertEqual(values.get("OTHER"), "keep")


if __name__ == "__main__":
    unittest.main()
