import http.server
import importlib.util
import ipaddress
import json
import os
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".test-artifacts"
SPEC = importlib.util.spec_from_file_location("one_guard_installer_models", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class StubJudge:
    """A loopback judge that answers GET /v1/models with a scripted body."""

    def __init__(self, body=None, status=200, delay=0.0, redirect=None,
                 content_type="application/json", context=None):
        self.requests = []
        self.body = body if body is not None else json.dumps(
            {"data": [{"id": "stub-model"}]}).encode("utf-8")
        self.status = status
        self.delay = delay
        self.redirect = redirect
        self.content_type = content_type
        judge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                judge.requests.append({"path": self.path, "headers": dict(self.headers)})
                if judge.delay:
                    time.sleep(judge.delay)
                if judge.redirect:
                    self.send_response(302)
                    self.send_header("Location", judge.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(judge.status)
                self.send_header("Content-Type", judge.content_type)
                self.send_header("Content-Length", str(len(judge.body)))
                self.end_headers()
                try:
                    self.wfile.write(judge.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if context is not None:
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self):
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def secure_origin(self):
        host, port = self.server.server_address[:2]
        return f"https://{host}:{port}"

    @property
    def endpoint(self):
        return self.endpoint_for("chat_completions")

    def endpoint_for(self, protocol):
        path = "/v1/systemone" if protocol == "systemone" else "/v1/chat/completions"
        return self.origin + path

    def secure_endpoint_for(self, protocol):
        path = "/v1/systemone" if protocol == "systemone" else "/v1/chat/completions"
        return self.secure_origin + path

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


HAVE_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None


def self_signed_certificate(directory):
    """Write a self-signed certificate for 127.0.0.1 and return the (cert, key) paths."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder()
                   .subject_name(name)
                   .issuer_name(name)
                   .public_key(key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName(
                       [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .sign(key, hashes.SHA256()))
    certificate_path = Path(directory) / "cert.pem"
    key_path = Path(directory) / "key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return certificate_path, key_path


class RecordingSocket:
    """A socket stand-in that keeps the bytes the client would send."""

    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def close(self):
        pass


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
        self.asked.append(("choose", title, [value for value, _ in choices],
                           [label for _, label in choices]))
        if not self.choices:
            return choices[default][0]
        choice = self.choices.pop(0)
        if choice not in [value for value, _ in choices]:
            raise AssertionError(f"Invalid scripted choice {choice!r} for {title}")
        return choice

    def text(self, label, default="", validate=str, secret=False, allow_empty=False):
        self.asked.append(("text", label, default))
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

    def menus(self, title):
        for entry in self.asked:
            if entry[0] == "choose" and entry[1] == title:
                return entry[2]
        raise AssertionError(f"Menu {title!r} was never shown")

    def menu_labels(self, title):
        for entry in self.asked:
            if entry[0] == "choose" and entry[1] == title:
                return entry[3]
        raise AssertionError(f"Menu {title!r} was never shown")


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="models-", dir=ARTIFACTS)
        self.addCleanup(lambda: self.assertFalse(Path(self.temp.name).exists()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "data"
        self.source = self.root / "source"
        (self.source / "hermes_cli").mkdir(parents=True)
        (self.source / "hermes_cli/__init__.py").write_text("")
        (self.source / "hermes").write_text("")
        self.release = self.root / "release"
        self.release.mkdir()
        (self.release / "plugin.yaml").write_text("name: one-guard\n")
        (self.release / "__init__.py").write_text("VERSION = 1\n")
        self.judge = StubJudge()
        self.addCleanup(self.judge.close)
        self.environment = {"HOME": str(self.root), "PATH": "", "HERMES_DIR": "",
                            "HERMES_HOME": str(self.home)}
        patch.object(installer, "RELEASE", self.release).start()
        self.addCleanup(patch.stopall)

    def run_wizard(self, ui, extra=(), environment=None):
        values = dict(self.environment)
        values.update(environment or {})
        protocol = "systemone" if ui.choices and ui.choices[0] == "systemone" else "chat_completions"
        ui.texts.setdefault("Judge endpoint", [self.judge.endpoint_for(protocol)])
        printed = []
        with patch.dict(os.environ, values, clear=True):
            with patch.object(installer.installer_ui, "create_ui", return_value=ui):
                with patch("builtins.print", side_effect=lambda *args, **kwargs:
                           printed.append(" ".join(str(item) for item in args))):
                    code = installer.main(["--hermes-dir", str(self.source), "--plain", *extra])
        self.printed = "\n".join(printed)
        return code, "\n".join(ui.said)

    def run_yes(self, extra=(), environment=None):
        values = dict(self.environment)
        values.update(environment or {})
        with patch.dict(os.environ, values, clear=True):
            with patch("builtins.print"):
                code = installer.main(["--hermes-dir", str(self.source), "--yes", *extra])
        return code

    def settings(self, home=None):
        import yaml
        target = (home or self.home) / "config.yaml"
        return yaml.safe_load(target.read_text())["plugins"]["entries"]["one-guard"]["settings"]

    def write_env(self, text):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / ".env").write_text(text)

    def write_config(self, text):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.yaml").write_text(text)

    def backup_files(self):
        files = {}
        for backup in sorted(self.home.glob(".one-guard-backup-*")):
            for path in sorted(backup.rglob("*")):
                if path.is_file():
                    files[f"{backup.name}/{path.name}"] = path.read_bytes()
        return files

    def test_without_consent_the_judge_is_never_contacted(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", False, "install"],
                        texts={"Judge model": "typed-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.judge.requests, [])
        self.assertEqual(self.settings()["judge_model"], "typed-model")

    def test_an_invalid_existing_model_skips_discovery_and_can_be_fixed_manually(self):
        import yaml
        credential = "secret-token"
        malicious = "model-secret-token"
        self.write_env(f'ONE_GUARD_TOKEN="{credential}"\n')
        self.judge.body = json.dumps({"data": [{"id": malicious}]}).encode()

        class ProbeUI(ScriptedUI):
            def choose(self, title, choices, default=0):
                if title == "Ask the judge for its model list?":
                    return True
                if title == "Which model should One Guard use?":
                    return malicious
                return super().choose(title, choices, default)

        for invalid in (42, malicious + "\u202e"):
            with self.subTest(model=invalid):
                self.write_config(yaml.safe_dump({"plugins": {"enabled": [], "entries": {"one-guard": {
                    "settings": {"judge_model": invalid}}}}}))
                ui = ProbeUI(choices=["chat_completions", False, "yes_no", "keep", "install"],
                             texts={"Judge model": "typed-model"})
                with patch.object(installer, "probe_models", wraps=installer.probe_models) as probe:
                    code, said = self.run_wizard(ui)
                self.assertEqual(code, 0, said)
                probe.assert_not_called()
                self.assertEqual(self.judge.requests, [])
                self.assertIn("Discovery skipped", said)
                self.assertNotIn("No credential is selected", said)
                self.assertNotIn(credential, said + self.printed + repr(ui.asked))
                self.assertIn(("text", "Judge model", ""), ui.asked)
                self.assertEqual(self.settings()["judge_model"], "typed-model")
                from dotenv import dotenv_values
                self.assertEqual(dotenv_values(self.home / ".env")["ONE_GUARD_TOKEN"], credential)

    def test_malformed_env_skips_discovery_without_echoing_a_model_default(self):
        import yaml
        credential = "secret-token"
        for raw in (b'ONE_GUARD_TOKEN="secret-token"\nBROKEN="unterminated\n',
                    b'ONE_GUARD_TOKEN="secret-token"\n\xff',
                    b'ONE_GUARD_TOKEN="secret-token"\nONE_GUARD_TOKEN="other"\n'):
            with self.subTest(raw=raw):
                self.write_env("")
                (self.home / ".env").write_bytes(raw)
                args = installer.trial_args(protocol="chat_completions", endpoint=self.judge.endpoint)
                ui = ScriptedUI(choices=[True, "model-secret-token"],
                                texts={"Judge model": "typed-model"})
                with patch.object(installer, "probe_models", wraps=installer.probe_models) as probe:
                    installer.configure_model(ui, args, {"judge_model": "model-secret-token"},
                                              self.home, None, yaml)
                probe.assert_not_called()
                self.assertEqual(self.judge.requests, [])
                self.assertIn("Discovery skipped", "\n".join(ui.said))
                self.assertNotIn(credential, repr(ui.said) + repr(ui.asked))
                self.assertIn(("text", "Judge model", ""), ui.asked)
                self.assertEqual(args.model, "typed-model")
                self.assertEqual((self.home / ".env").read_bytes(), raw)
                self.assertFalse((self.home / "config.yaml").exists())

    def test_credential_read_permission_errors_skip_discovery_without_leaking_details(self):
        import yaml
        credential = "secret-token"
        self.write_env(f'ONE_GUARD_TOKEN="{credential}"\n')
        self.write_config("{}\n")
        read_regular = installer.read_regular
        for filename in ("config.yaml", ".env"):
            with self.subTest(filename=filename):
                def denied(path):
                    if path == self.home / filename:
                        raise PermissionError(f"Cannot read {credential}")
                    return read_regular(path)

                args = installer.trial_args(protocol="chat_completions", endpoint=self.judge.endpoint)
                ui = ScriptedUI(choices=[True, "model-secret-token"],
                                texts={"Judge model": "typed-model"})
                with patch.object(installer, "read_regular", side_effect=denied):
                    with patch.object(installer, "probe_models", wraps=installer.probe_models) as probe:
                        installer.configure_model(ui, args, {"judge_model": "model-secret-token"},
                                                  self.home, None, yaml)
                probe.assert_not_called()
                self.assertEqual(self.judge.requests, [])
                self.assertIn("Discovery skipped", "\n".join(ui.said))
                self.assertNotIn(credential, repr(ui.said) + repr(ui.asked))
                self.assertIn(("text", "Judge model", ""), ui.asked)
                self.assertEqual(args.model, "typed-model")
                self.assertEqual((self.home / "config.yaml").read_text(), "{}\n")
                self.assertEqual((self.home / ".env").read_text(),
                                 f'ONE_GUARD_TOKEN="{credential}"\n')

    def test_consent_probes_the_models_path_and_lists_the_identifiers(self):
        self.judge.body = json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-b",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual([request["path"] for request in self.judge.requests], ["/v1/models"])
        self.assertEqual(ui.menus("Which model should One Guard use?")[:2], ["model-a", "model-b"])
        self.assertEqual(self.settings()["judge_model"], "model-b")

    def test_yes_install_never_contacts_the_judge(self):
        code = self.run_yes(["--hermes-home", str(self.home)])
        self.assertEqual(code, 0)
        self.assertEqual(self.judge.requests, [])

    def test_environment_credential_is_sent_to_the_judge(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": "env-secret"})
        self.assertEqual(code, 0, said)
        self.assertEqual(self.judge.requests[0]["headers"]["Authorization"], "Bearer env-secret")

    def test_env_file_credential_is_sent_to_the_judge(self):
        self.write_env('ONE_GUARD_TOKEN="file-secret"\n')
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.judge.requests[0]["headers"]["Authorization"], "Bearer file-secret")

    def test_legacy_yaml_credential_is_migrated_and_sent_to_the_judge(self):
        self.write_config("plugins:\n  entries:\n    one-guard:\n      settings:\n"
                          "        token: legacy-secret\n")
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.judge.requests[0]["headers"]["Authorization"], "Bearer legacy-secret")
        from dotenv import dotenv_values
        self.assertEqual(dotenv_values(self.home / ".env")["ONE_GUARD_TOKEN"], "legacy-secret")
        self.assertNotIn("token", self.settings())

    def test_replaced_credential_is_the_one_that_is_sent(self):
        self.write_env('ONE_GUARD_TOKEN="old-secret"\n')
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "replace", True, "stub-model",
                                 "install"],
                        texts={"New bearer credential  (blank removes it)": "new-secret"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.judge.requests[0]["headers"]["Authorization"], "Bearer new-secret")
        self.assertNotIn("new-secret", said)

    def test_cleared_credential_is_not_sent(self):
        self.write_env('ONE_GUARD_TOKEN="old-secret"\n')
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "clear", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertNotIn("Authorization", self.judge.requests[0]["headers"])

    def test_a_redirect_is_not_followed_and_leaks_no_credential(self):
        other = StubJudge()
        self.addCleanup(other.close)
        self.judge.redirect = other.origin + "/v1/models"
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"],
                        texts={"Judge model": "typed-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(other.requests, [])
        self.assertEqual(len(self.judge.requests), 1)
        self.assertIn("could not be read", said)

    def test_ambient_proxy_settings_are_ignored(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"http_proxy": "http://127.0.0.1:1",
                                                       "HTTP_PROXY": "http://127.0.0.1:1",
                                                       "https_proxy": "http://127.0.0.1:1"})
        self.assertEqual(code, 0, said)
        self.assertEqual(len(self.judge.requests), 1)

    def test_an_oversized_response_is_rejected(self):
        self.judge.body = b"{" + b"x" * 200000
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"],
                        texts={"Judge model": "typed-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertIn("could not be read", said)
        self.assertEqual(self.settings()["judge_model"], "typed-model")

    def test_a_slow_judge_times_out_and_manual_input_still_works(self):
        self.judge.delay = 7
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"],
                        texts={"Judge model": "typed-model"})
        started = time.monotonic()
        code, said = self.run_wizard(ui)
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, said)
        self.assertIn("could not be read", said)
        self.assertLess(elapsed, 13)
        self.assertEqual(self.settings()["judge_model"], "typed-model")

    def test_an_invalid_body_falls_back_to_manual_input(self):
        self.judge.body = b"this is not json"
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"],
                        texts={"Judge model": "typed-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertIn("could not be read", said)
        self.assertNotIn("this is not json", said)
        self.assertEqual(self.settings()["judge_model"], "typed-model")

    def test_malformed_identifiers_are_dropped_and_never_shown(self):
        self.judge.body = json.dumps({"data": [
            {"id": "good-model"},
            {"id": "bad\x1bmodel"},
            {"id": 42},
            {"id": ""},
            {"id": "good-model"},
        ]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "good-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertIn("good-model", offered)
        self.assertNotIn("bad\x1bmodel", offered)
        self.assertNotIn("\x1b", said)
        self.assertEqual(len(offered), len(set(offered)))

    def test_an_empty_model_list_falls_back_to_manual_input(self):
        self.judge.body = json.dumps({"data": []}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"],
                        texts={"Judge model": "typed-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertIn("no usable model identifiers", said)
        self.assertEqual(self.settings()["judge_model"], "typed-model")

    def test_the_manual_option_names_the_model(self):
        self.judge.body = json.dumps({"data": [{"id": "model-a"}]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True,
                                 installer.MANUAL_MODEL, "install"],
                        texts={"Judge model": "manual-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.settings()["judge_model"], "manual-model")

    def test_the_keep_option_preserves_the_configured_model(self):
        self.write_config("plugins:\n  entries:\n    one-guard:\n      settings:\n"
                          "        judge_model: kept-model\n")
        self.judge.body = json.dumps({"data": [{"id": "model-a"}]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True,
                                 installer.KEEP_MODEL, "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.settings()["judge_model"], "kept-model")

    def test_an_identifier_equal_to_the_credential_is_never_offered(self):
        self.judge.body = json.dumps({"data": [{"id": "secret-token"}, {"id": "model-a"}]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-a",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": "secret-token"})
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertNotIn("secret-token", offered)
        self.assertNotIn("secret-token", said)

    def test_the_review_can_reopen_the_model_step(self):
        self.judge.body = json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-a",
                                 "edit", "model", True, "model-b", "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.settings()["judge_model"], "model-b")

    def test_the_probe_targets_the_plaintext_endpoint_only_after_approval(self):
        recorded = []

        def fake_probe(endpoint, token, timeout=installer.DISCOVERY_TIMEOUT):
            recorded.append((endpoint, token))
            return {"entries": [{"id": "model-a", "native": None}], "truncated": False}

        ui = ScriptedUI(choices=["systemone", True, "yes_no", "keep", True, "model-a", "install"],
                        texts={"Judge endpoint": "http://192.0.2.1:9292/v1/systemone"})
        with patch.object(installer, "probe_models", fake_probe):
            code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertEqual(recorded, [("http://192.0.2.1:9292/v1/systemone", None)])
        self.assertIn("allow_insecure_http", self.settings())
        self.assertTrue(self.settings()["allow_insecure_http"])

    def test_a_plaintext_remote_endpoint_is_refused_without_approval(self):
        recorded = []

        def fake_probe(endpoint, token, timeout=installer.DISCOVERY_TIMEOUT):
            recorded.append(endpoint)
            return None

        ui = ScriptedUI(choices=["systemone", False, "yes_no", "keep", "install"],
                        texts={"Judge endpoint": "http://192.0.2.1:9292/v1/systemone"})
        with patch.object(installer, "probe_models", fake_probe):
            code, said = self.run_wizard(ui)
        self.assertEqual(code, 1)
        self.assertEqual(recorded, [])
        self.assertFalse(self.home.exists())

    def test_system_one_labels_come_from_the_server_metadata(self):
        self.judge.body = json.dumps({"data": [
            {"id": "native-model", "architecture": {"output_modalities": ["text", "decisions"]}},
            {"id": "text-model", "architecture": {"output_modalities": ["text"]}},
            {"id": "plain-model"},
        ]}).encode()
        ui = ScriptedUI(choices=["systemone", False, "yes_no", "keep", True, "native-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        labels = ui.menu_labels("Which model should One Guard use?")
        self.assertIn("native-model", labels[0])
        self.assertIn("native decisions reported by the server", labels[0])
        self.assertIn("text-model", labels[1])
        self.assertIn("text only per server metadata", labels[1])
        self.assertIn("plain-model", labels[2])
        self.assertIn("capability not reported by the server", labels[2])

    def test_chat_completions_never_claims_compatibility(self):
        self.judge.body = json.dumps({"data": [
            {"id": "native-model", "architecture": {"output_modalities": ["text", "decisions"]}},
        ]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "native-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        labels = ui.menu_labels("Which model should One Guard use?")
        self.assertIn("compatibility not declared by the server", labels[0])
        self.assertNotIn("native decisions reported", said)

    def test_the_model_list_is_capped_and_the_limit_is_stated(self):
        self.judge.body = json.dumps({"data": [{"id": f"model-{index}"} for index in range(100)]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-0",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertEqual(len(offered), installer.MAX_DISCOVERY_MODELS + 2)
        self.assertIn("does not follow pagination", said)

    def test_overlong_identifiers_are_dropped(self):
        self.judge.body = json.dumps({"data": [
            {"id": "x" * (installer.MAX_MODEL_ID_LENGTH + 1)},
            {"id": "good-model"},
        ]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "good-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertIn("good-model", offered)
        self.assertNotIn("x" * (installer.MAX_MODEL_ID_LENGTH + 1), offered)

    def test_nothing_is_written_before_the_review_is_confirmed(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "cancel"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.judge.requests), 1)
        self.assertFalse((self.home / "config.yaml").exists())
        self.assertFalse((self.home / "plugins").exists())

    def test_an_identifier_holding_the_credential_is_never_offered_or_committed(self):
        credential = "secret-token"
        self.judge.body = json.dumps({"data": [
            {"id": "model-secret-token-x"},
            {"id": "model-a"},
        ]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-a",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": credential})
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertNotIn("model-secret-token-x", offered)
        self.assertIn("model-a", offered)
        self.assertNotIn("model-secret-token-x", said)
        self.assertNotIn(credential, said)
        self.assertNotIn(credential, self.printed)
        self.assertEqual(self.settings()["judge_model"], "model-a")
        config = (self.home / "config.yaml").read_text()
        self.assertNotIn(credential, config)
        self.assertNotIn("model-secret-token-x", config)
        for name, data in self.backup_files().items():
            if name.endswith("/.env") or name.endswith("/new-.env"):
                continue
            self.assertNotIn(credential.encode(), data, name)
            self.assertNotIn(b"model-secret-token-x", data, name)
        from dotenv import dotenv_values
        self.assertEqual(dotenv_values(self.home / ".env")["ONE_GUARD_TOKEN"], credential)

    def test_a_short_credential_suppresses_every_matching_identifier(self):
        self.judge.body = json.dumps({"data": [{"id": "model-x"}, {"id": "model-y"}]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-y",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": "x"})
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertNotIn("model-x", offered)
        self.assertIn("model-y", offered)
        self.assertEqual(self.settings()["judge_model"], "model-y")

    def test_a_credential_bearing_selection_is_refused_before_it_is_written(self):
        credential = "secret-token"
        self.judge.body = json.dumps({"data": [{"id": "model-a"}]}).encode()

        class HostileUI(ScriptedUI):
            def choose(self, title, choices, default=0):
                if title == "Which model should One Guard use?":
                    self.asked.append(("choose", title, [value for value, _ in choices],
                                       [label for _, label in choices]))
                    return "model-secret-token"
                return super().choose(title, choices, default)

        ui = HostileUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": credential})
        self.assertEqual(code, 1)
        self.assertNotIn(credential, said)
        self.assertNotIn(credential, self.printed)
        self.assertFalse((self.home / "config.yaml").exists())

    def test_a_stored_model_holding_the_credential_is_not_offered_again(self):
        credential = "secret-token"
        self.write_config("plugins:\n  entries:\n    one-guard:\n      settings:\n"
                          "        judge_model: model-secret-token\n")
        self.judge.body = json.dumps({"data": [{"id": "model-a"}]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True,
                                 installer.KEEP_MODEL, "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": credential})
        self.assertEqual(code, 0, said)
        labels = ui.menu_labels("Which model should One Guard use?")
        self.assertNotIn("model-secret-token", " ".join(labels))
        self.assertEqual(self.settings()["judge_model"], "")

    def test_a_manual_identifier_with_the_credential_is_accepted_but_not_echoed(self):
        credential = "secret-token"
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True,
                                 installer.MANUAL_MODEL, "install"],
                        texts={"Judge model": "model-secret-token"})
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": credential})
        self.assertEqual(code, 0, said)
        self.assertIn("contains the bearer credential", said)
        self.assertNotIn(credential, self.printed)
        self.assertEqual(self.settings()["judge_model"], "model-secret-token")
        renderer = installer.installer_ui.PlainUI()
        renderer.secrets.append(credential)
        self.assertNotIn(credential, renderer.clean("model-secret-token"))

    def test_a_deeply_nested_response_falls_back_to_manual_entry(self):
        self.judge.body = b"[" * 2000
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"],
                        texts={"Judge model": "typed-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertIn("could not be read", said)
        self.assertEqual(self.settings()["judge_model"], "typed-model")

    def test_a_deeply_nested_response_and_a_cancel_write_nothing(self):
        self.judge.body = b"[" * 2000
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "cancel"],
                        texts={"Judge model": "typed-model"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 1)
        self.assertFalse(self.home.exists())

    def test_the_consent_warns_that_remote_plaintext_http_exposes_the_credential(self):
        def fake_probe(endpoint, token, timeout=installer.DISCOVERY_TIMEOUT):
            return {"entries": [{"id": "model-a", "native": None}], "truncated": False}

        ui = ScriptedUI(choices=["systemone", True, "yes_no", "keep", True, "model-a", "install"],
                        texts={"Judge endpoint": "http://192.0.2.1:9292/v1/systemone"})
        with patch.object(installer, "probe_models", fake_probe):
            code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": "env-secret"})
        self.assertEqual(code, 0, said)
        self.assertIn("remote plaintext HTTP", said)
        self.assertIn("unencrypted", said)
        self.assertNotIn("env-secret", said)

    def test_the_consent_warns_that_a_plaintext_probe_is_visible_without_a_credential(self):
        def fake_probe(endpoint, token, timeout=installer.DISCOVERY_TIMEOUT):
            return {"entries": [{"id": "model-a", "native": None}], "truncated": False}

        ui = ScriptedUI(choices=["systemone", True, "yes_no", "keep", True, "model-a", "install"],
                        texts={"Judge endpoint": "http://192.0.2.1:9292/v1/systemone"})
        with patch.object(installer, "probe_models", fake_probe):
            code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertIn("no authorization header", said)
        self.assertIn("visible", said)
        self.assertIn("remote plaintext HTTP", said)

    def test_a_loopback_plaintext_endpoint_is_not_reported_as_remote(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": "env-secret"})
        self.assertEqual(code, 0, said)
        self.assertNotIn("remote plaintext HTTP", said)
        self.assertNotIn("unencrypted", said)

    def test_the_credential_is_described_as_the_one_this_installer_selected(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": "env-secret"})
        self.assertEqual(code, 0, said)
        self.assertIn("selected by this installer", said)
        self.assertIn("may resolve a different credential at runtime", said)

    def test_modalities_are_reported_only_when_the_server_is_explicit(self):
        self.judge.body = json.dumps({"data": [
            {"id": "decisions-only", "architecture": {"output_modalities": ["decisions"]}},
            {"id": "both", "architecture": {"output_modalities": ["text", "decisions"]}},
            {"id": "text-only", "architecture": {"output_modalities": ["text"]}},
            {"id": "text-twice", "architecture": {"output_modalities": ["text", "text"]}},
            {"id": "decisions-twice",
             "architecture": {"output_modalities": ["decisions", "decisions"]}},
            {"id": "mixed", "architecture": {"output_modalities": ["text", "image"]}},
            {"id": "malformed", "architecture": {"output_modalities": ["text", 7]}},
            {"id": "empty", "architecture": {"output_modalities": []}},
            {"id": "not-a-list", "architecture": {"output_modalities": "decisions"}},
            {"id": "unrelated", "architecture": {"input_modalities": ["text"]}},
        ]}).encode()
        ui = ScriptedUI(choices=["systemone", False, "yes_no", "keep", True, "decisions-only",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        labels = {}
        for label in ui.menu_labels("Which model should One Guard use?"):
            name, _, note = label.partition("  ·  ")
            labels[name] = note
        self.assertIn("native decisions reported by the server", labels["decisions-only"])
        self.assertIn("native decisions reported by the server", labels["both"])
        self.assertIn("text only per server metadata", labels["text-only"])
        self.assertIn("native decisions reported by the server", labels["decisions-twice"])
        for name in ("mixed", "malformed", "empty", "not-a-list", "unrelated", "text-twice"):
            self.assertIn("capability not reported by the server", labels[name], name)

    def test_capability_metadata_is_matched_exactly(self):
        self.judge.body = json.dumps({"data": [
            {"id": "exact-text", "architecture": {"output_modalities": ["text"]}},
            {"id": "exact-decisions",
             "architecture": {"output_modalities": ["text", "decisions"]}},
            {"id": "upper-text", "architecture": {"output_modalities": ["TEXT"]}},
            {"id": "upper-decisions", "architecture": {"output_modalities": ["Decisions"]}},
            {"id": "padded-text", "architecture": {"output_modalities": [" text "]}},
            {"id": "padded-decisions", "architecture": {"output_modalities": [" decisions "]}},
            {"id": "repeated-text", "architecture": {"output_modalities": ["text", "text"]}},
            {"id": "malformed-decisions",
             "architecture": {"output_modalities": ["decisions", 7]}},
        ]}).encode()
        ui = ScriptedUI(choices=["systemone", False, "yes_no", "keep", True, "exact-decisions",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        labels = {}
        for label in ui.menu_labels("Which model should One Guard use?"):
            name, _, note = label.partition("  ·  ")
            labels[name] = note
        self.assertIn("text only per server metadata", labels["exact-text"])
        self.assertIn("native decisions reported by the server", labels["exact-decisions"])
        for name in ("upper-text", "upper-decisions", "padded-text", "padded-decisions",
                     "repeated-text", "malformed-decisions"):
            self.assertIn("capability not reported by the server", labels[name], name)

    def test_unicode_format_control_identifiers_are_dropped(self):
        self.judge.body = json.dumps({"data": [
            {"id": "good-model"},
            {"id": "rtl\u202emodel"},
            {"id": "zero\u200bwidth"},
            {"id": "private\ue000use"},
        ]}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "good-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertIn("good-model", offered)
        self.assertEqual(len(offered), 3)
        self.assertNotIn("\u202e", said)

    def test_a_model_with_a_unicode_format_control_is_refused(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", False, "install"],
                        texts={"Judge model": "model\u202ename"})
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 1)
        self.assertNotIn("\u202e", said)
        self.assertFalse(self.home.exists())

    def test_dropped_identifiers_do_not_count_towards_the_truncation_limit(self):
        data = [{"id": f"model-{index}"} for index in range(40)]
        data += [{"id": f"bad\x1b{index}"} for index in range(40)]
        data += [{"id": 42} for _ in range(40)]
        self.judge.body = json.dumps({"data": data}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-0",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertEqual(len(offered), 42)
        self.assertNotIn("more models than this probe shows", said)

    def test_an_exhausted_probe_slot_returns_nothing_and_asks_nobody(self):
        exhausted = threading.BoundedSemaphore(0)
        with patch.object(installer, "PROBE_SLOTS", exhausted):
            started = time.monotonic()
            result = installer.probe_models(self.judge.endpoint, "env-secret", timeout=0.2)
        self.assertIsNone(result)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.judge.requests, [])

    def test_a_probe_that_falls_back_never_explains_the_internals(self):
        exhausted = threading.BoundedSemaphore(0)
        with patch.object(installer, "PROBE_SLOTS", exhausted):
            ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "install"],
                            texts={"Judge model": "typed-model"})
            code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        self.assertIn("could not be read", said)
        self.assertNotIn("semaphore", said.lower())
        self.assertNotIn("slot", said.lower())
        self.assertEqual(self.settings()["judge_model"], "typed-model")

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography is required for the TLS stub")
    def test_a_self_signed_certificate_is_rejected(self):
        certificate, key = self.self_signed_certificate()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        judge = StubJudge(context=context)
        self.addCleanup(judge.close)
        created = []
        real = ssl.create_default_context

        def spy(*args, **kwargs):
            context = real(*args, **kwargs)
            created.append(context)
            return context

        with patch.object(installer.ssl, "create_default_context", spy):
            result = installer.probe_models(judge.secure_endpoint_for("chat_completions"),
                                            "env-secret")
        self.assertIsNone(result)
        self.assertTrue(created)
        self.assertTrue(created[0].check_hostname)
        self.assertEqual(created[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(judge.requests, [])

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography is required for the TLS stub")
    def test_a_trusted_certificate_is_accepted_over_tls(self):
        certificate, key = self.self_signed_certificate()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        judge = StubJudge(context=context)
        self.addCleanup(judge.close)
        trusting = ssl.create_default_context(cafile=str(certificate))
        with patch.object(installer.ssl, "create_default_context", lambda *a, **k: trusting):
            result = installer.probe_models(judge.secure_endpoint_for("chat_completions"),
                                            "env-secret")
        self.assertIsNotNone(result)
        self.assertEqual([entry["id"] for entry in result["entries"]], ["stub-model"])
        self.assertEqual(judge.requests[0]["headers"]["Authorization"], "Bearer env-secret")

    def self_signed_certificate(self):
        directory = self.root / "tls"
        directory.mkdir(exist_ok=True)
        return self_signed_certificate(directory)

    def send_request(self, host, port, secure):
        """Return the raw request bytes a deadline connection would put on the wire."""
        connection = installer.DeadlineConnection(host, port, time.monotonic() + 5, secure)
        connection.sock = RecordingSocket()
        connection.request("GET", "/v1/models", headers={"Accept": "application/json"})
        return connection.sock.sent

    def test_credential_bearing_identifiers_do_not_consume_the_identifier_limit(self):
        credential = "secret-token"
        data = [{"id": f"model-secret-token-{index}"} for index in range(64)]
        data += [{"id": "model-a"}, {"id": "model-b"}]
        self.judge.body = json.dumps({"data": data}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-a",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": credential})
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertEqual(offered[:2], ["model-a", "model-b"])
        self.assertNotIn("more models than this probe shows", said)
        self.assertNotIn(credential, said)
        self.assertNotIn(credential, self.printed)

    def test_a_short_credential_does_not_hide_the_identifiers_that_follow(self):
        credential = "x"
        data = [{"id": f"evil-x-{index}"} for index in range(64)]
        data += [{"id": "model-a"}, {"id": "model-b"}]
        self.judge.body = json.dumps({"data": data}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-b",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": credential})
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertEqual(offered[:2], ["model-a", "model-b"])
        self.assertNotIn("more models than this probe shows", said)
        self.assertNotIn("evil-x", " ".join(offered))
        self.assertNotIn(credential, self.printed)

    def test_truncation_counts_only_the_identifiers_that_survive_the_filter(self):
        credential = "secret-token"
        data = [{"id": f"model-secret-token-{index}"} for index in range(64)]
        data += [{"id": f"model-{index}"} for index in range(70)]
        self.judge.body = json.dumps({"data": data}).encode()
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "model-0",
                                 "install"])
        code, said = self.run_wizard(ui, environment={"ONE_GUARD_TOKEN": credential})
        self.assertEqual(code, 0, said)
        offered = ui.menus("Which model should One Guard use?")
        self.assertEqual(len(offered), installer.MAX_DISCOVERY_MODELS + 2)
        self.assertIn("more models than this probe shows", said)
        self.assertNotIn("model-secret-token", " ".join(offered))

    def test_parse_model_list_drops_the_credential_before_the_limit(self):
        data = [{"id": f"model-secret-{index}"} for index in range(64)]
        data += [{"id": "model-a"}]
        raw = json.dumps({"data": data}).encode()
        filtered = installer.parse_model_list(raw, "secret")
        self.assertEqual([entry["id"] for entry in filtered["entries"]], ["model-a"])
        self.assertFalse(filtered["truncated"])
        unfiltered = installer.parse_model_list(raw)
        self.assertEqual(len(unfiltered["entries"]), installer.MAX_DISCOVERY_MODELS)
        self.assertTrue(unfiltered["truncated"])

    def test_the_probe_drops_identifiers_that_hold_the_credential(self):
        self.judge.body = json.dumps({"data": [
            {"id": "model-secret"},
            {"id": "model-a"},
        ]}).encode()
        result = installer.probe_models(self.judge.endpoint, "secret")
        self.assertEqual([entry["id"] for entry in result["entries"]], ["model-a"])

    def test_the_host_header_omits_the_default_https_port(self):
        sent = self.send_request("judge.example", 443, True)
        self.assertIn(b"Host: judge.example\r\n", sent)
        self.assertNotIn(b":443", sent)

    def test_the_host_header_omits_the_default_http_port(self):
        sent = self.send_request("judge.example", 80, False)
        self.assertIn(b"Host: judge.example\r\n", sent)
        self.assertNotIn(b":80", sent)

    def test_a_non_default_port_stays_in_the_host_header(self):
        sent = self.send_request("judge.example", 8443, True)
        self.assertIn(b"Host: judge.example:8443\r\n", sent)

    def test_the_stub_judge_sees_the_requested_host(self):
        ui = ScriptedUI(choices=["chat_completions", False, "yes_no", "keep", True, "stub-model",
                                 "install"])
        code, said = self.run_wizard(ui)
        self.assertEqual(code, 0, said)
        host, port = self.judge.server.server_address[:2]
        self.assertEqual(self.judge.requests[0]["headers"]["Host"], f"{host}:{port}")


if __name__ == "__main__":
    unittest.main()
