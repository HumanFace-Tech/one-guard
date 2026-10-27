import argparse
import copy
import hashlib
import importlib.util
import ipaddress
import io
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


PLUGIN = "one-guard"
MARKER = ".one-guard-install.json"
RELEASE = Path(__file__).resolve().parents[1] / "plugins" / PLUGIN


def _load_sibling(name):
    path = Path(__file__).resolve().parent / (name + ".py")
    spec = importlib.util.spec_from_file_location("one_guard_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer_ui = _load_sibling("installer_ui")


class InstallError(ValueError):
    pass


class Cancelled(InstallError):
    pass


def safe_path(path):
    path = Path(os.path.abspath(path.expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise InstallError(f"Refusing symlink: {part}")
    return path


def read_regular(path):
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise InstallError(f"Expected a regular, singly linked file: {path}")
        return stream.read()


def is_source(path):
    return (path / "hermes_cli" / "__init__.py").is_file() and (
        (path / "hermes").is_file() or (path / "pyproject.toml").is_file()
    )


def discover_sources():
    home = Path.home()
    guesses = []
    if os.environ.get("HERMES_DIR"):
        guesses.append(Path(os.environ["HERMES_DIR"]).expanduser())
    executable = shutil.which("hermes")
    if executable:
        resolved = Path(executable).resolve()
        guesses.extend([resolved.parent, resolved.parent.parent, resolved.parent.parent.parent])
    guesses.extend([home / ".hermes/hermes-agent", home / "hermes-agent", home / ".local/share/hermes/hermes-agent"])
    found = []
    for candidate in guesses:
        candidate = candidate.resolve()
        if is_source(candidate) and candidate not in found:
            found.append(candidate)
    return found


def ask(message):
    if not sys.stdin.isatty():
        raise InstallError(f"{message} No interactive stdin; supply --hermes-dir and --yes.")
    try:
        return input(message).strip()
    except EOFError as exc:
        raise InstallError("Input ended before installation was confirmed.") from exc


def select_source(explicit, ui=None, noninteractive=False):
    if explicit:
        return validate_source(explicit)
    if ui is not None:
        return choose_source(ui)
    found = discover_sources()
    if len(found) == 1:
        return found[0]
    if noninteractive:
        raise InstallError("Hermes source missing or ambiguous; supply --hermes-dir with --yes.")
    if not found:
        answer = ask("Hermes source not found. Enter the Hermes source directory: ")
        if not answer:
            raise InstallError("A Hermes source directory is required.")
        return select_source(answer)
    for index, path in enumerate(found, 1):
        print(f"{index}. {installer_ui.safe_text(path)}")
    choice = ask("Select a Hermes source number (or use --hermes-dir): ")
    if not choice.isdecimal() or not 1 <= int(choice) <= len(found):
        raise InstallError("Invalid Hermes source selection.")
    return found[int(choice) - 1]


def choose_source(ui):
    ui.header("01 / Hermes source", "Choose an existing Hermes checkout. This is separate from its data home.")
    found = discover_sources()
    while True:
        choices = [(str(path), str(path)) for path in found]
        choices.append((None, "Enter a different path"))
        choice = ui.choose("Hermes source", choices)
        if choice is not None:
            return validate_source(choice)
        answer = ui.text("Source directory", validate=validate_source, allow_empty=True)
        if answer is None:
            continue
        return answer


def load_yaml_module(source, argv, prefer_ui=False):
    yaml = None
    try:
        import yaml
        import dotenv
    except ImportError:
        yaml = None
    probe_imports = "import yaml, dotenv" + (", prompt_toolkit" if prefer_ui else "")
    if yaml is not None and (not prefer_ui or installer_ui.rich_available()):
        return yaml
    python = source / ".venv/bin/python"
    if not os.environ.get("ONE_GUARD_INSTALL_REEXEC") and python.is_file():
        probe = subprocess.run([str(python), "-c", probe_imports], capture_output=True)
        if probe.returncode == 0:
            env = dict(os.environ, ONE_GUARD_INSTALL_REEXEC="1")
            os.execve(str(python), [str(python), str(Path(__file__).resolve()), *argv,
                                    "--hermes-dir", str(source)], env)
    if yaml is not None:
        return yaml
    raise InstallError("PyYAML is required together with python-dotenv in the current Python or Hermes .venv. Activate an environment with these dependencies, then rerun sh ./install.sh.")


def parse_config(raw, yaml):
    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        if not isinstance(node, yaml.MappingNode):
            raise InstallError("Malformed YAML mapping in config.yaml.")
        seen = set()
        merge_seen = False
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                if merge_seen:
                    raise InstallError("Duplicate YAML merge key; fix config.yaml before installing.")
                merge_seen = True
                continue
            key = loader.construct_object(key_node, deep=True)
            try:
                if key in seen:
                    raise InstallError("Duplicate YAML mapping key; fix config.yaml before installing.")
                seen.add(key)
            except TypeError as exc:
                raise InstallError("YAML mapping keys must be hashable scalars.") from exc
        for _, value_node in node.value:
            loader.construct_object(value_node, deep=True)
        loader.flatten_mapping(node)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=True)

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        config = yaml.load(raw.decode("utf-8"), Loader=StrictLoader) if raw.strip() else {}
    except (yaml.YAMLError, UnicodeError, ValueError, RecursionError) as exc:
        raise InstallError("Malformed config.yaml; fix it before installing.") from exc
    if not isinstance(config, dict):
        raise InstallError("config.yaml must be a mapping.")
    return copy.deepcopy(config)


def submap(parent, key):
    if key not in parent:
        parent[key] = {}
    if not isinstance(parent[key], dict):
        raise InstallError(f"Configuration {key} must be a mapping.")
    parent[key] = copy.deepcopy(parent[key])
    return parent[key]


def preserve_legacy_plugins(config, home, yaml):
    plugins = submap(config, "plugins")
    if "enabled" in plugins:
        return
    version = config.get("_config_version")
    if version is not None and (type(version) is not int or version < 0):
        raise InstallError("Invalid _config_version; explicitly review plugins.enabled before installing.")
    if version is not None and version >= 21:
        plugins["enabled"] = []
        return
    disabled = plugins.get("disabled", [])
    if not isinstance(disabled, list) or any(type(item) is not str for item in disabled):
        raise InstallError("plugins.disabled must be a list of strings.")
    names = []
    directory = home / "plugins"
    if directory.exists():
        for child in sorted(directory.iterdir()):
            safe_path(child)
            if not child.is_dir():
                continue
            manifest = next((child / name for name in ("plugin.yaml", "plugin.yml")
                             if (child / name).exists()), None)
            if manifest is None:
                if (child / "plugin.json").exists() or any(child.glob("*/plugin.y*ml")):
                    raise InstallError("Ambiguous legacy plugins: run Hermes config migration and explicitly review plugins.enabled before installing.")
                continue
            if version is None:
                raise InstallError("Unversioned config with installed plugins and no plugins.enabled is ambiguous; explicitly review and set plugins.enabled before installing.")
            data = parse_config(read_regular(manifest), yaml)
            name = data.get("name") or child.name
            if type(name) is not str or not name.strip() or name in names:
                raise InstallError("Ambiguous legacy plugin names: explicitly review plugins.enabled before installing.")
            if name not in disabled:
                names.append(name)
    plugins["enabled"] = names


def merge_secret(raw, token, legacy, clear=False):
    from dotenv.parser import parse_stream
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise InstallError(".env must be UTF-8; fix its encoding before installing.") from exc
    bindings = list(parse_stream(io.StringIO(text)))
    if any(item.error for item in bindings):
        raise InstallError("Malformed .env; fix it before installing.")
    existing = [item for item in bindings if item.key == "ONE_GUARD_TOKEN"]
    if len(existing) > 1 or any(item.value is None or "\n" in item.original.string.strip()
                                for item in existing):
        raise InstallError("Ambiguous ONE_GUARD_TOKEN in .env; keep one single-line assignment before installing.")
    if clear:
        output = "".join(item.original.string for item in bindings
                         if item.key != "ONE_GUARD_TOKEN")
        return output.encode("utf-8")
    if token is None:
        token = existing[0].value if existing else legacy
    if token is None:
        return raw
    if (type(token) is not str or len(token) > 16384
            or any(ord(char) < 32 or ord(char) > 126 for char in token)):
        raise InstallError("Judge token must contain only printable ASCII characters (maximum 16384).")
    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    if "${" in token:
        raise InstallError("Judge token cannot contain interpolation syntax in Hermes .env; supply a literal credential without dollar-open-brace sequences.")
    line = 'ONE_GUARD_TOKEN="' + escaped + '"\n'
    output = "".join(line if item.key == "ONE_GUARD_TOKEN" else item.original.string
                     for item in bindings)
    if not existing:
        output += ("\n" if output and not output.endswith("\n") else "") + line
    return output.encode("utf-8")


def endpoint_error(path, secure_only):
    if secure_only:
        return (f"This endpoint must use {path} for the selected protocol and use HTTPS "
                "or a literal loopback address. A plain http:// address on another host "
                "needs the plaintext HTTP option turned on above.")
    return (f"Endpoint must use {path} for the selected protocol over HTTPS or loopback HTTP, "
            "without credentials, query or fragment. Remote HTTP requires --allow-insecure-http "
            "or allow_insecure_http: true.")


def merge_config(config, args):
    plugins = submap(config, "plugins")
    for key in ("enabled", "disabled"):
        values = plugins.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise InstallError(f"plugins.{key} must be a list of strings.")
        if len(set(values)) != len(values):
            raise InstallError(f"plugins.{key} contains duplicate entries.")
        plugins[key] = list(values)
    if PLUGIN not in plugins["enabled"]:
        plugins["enabled"].append(PLUGIN)
    plugins["disabled"] = [item for item in plugins["disabled"] if item != PLUGIN]
    settings = submap(submap(submap(plugins, "entries"), PLUGIN), "settings")
    if args.protocol is not None:
        settings["protocol"] = args.protocol
    settings.setdefault("protocol", "chat_completions")
    if settings["protocol"] not in ("chat_completions", "systemone"):
        raise InstallError("one-guard protocol must be chat_completions or systemone.")
    path = "/v1/systemone" if settings["protocol"] == "systemone" else "/v1/chat/completions"
    if args.allow_insecure_http:
        settings["allow_insecure_http"] = True
    for key, option, default in (("endpoint", "endpoint", "http://127.0.0.1:8080" + path),
                                 ("mode", "response_mode", "yes_no")):
        value = getattr(args, option)
        if value is not None:
            settings[key] = value
        elif key not in settings:
            settings[key] = default
    if args.model is not None:
        settings["judge_model"] = args.model
    if args.timeout is not None:
        settings["timeout"] = args.timeout
    if getattr(args, "threshold", None) is not None:
        settings["threshold"] = args.threshold
    if getattr(args, "insecure_override", None) is not None:
        settings["allow_insecure_http"] = args.insecure_override
    settings.pop("token", None)
    for key in ("endpoint", "judge_model", "mode", "prompt", "protocol"):
        if key in settings and (not isinstance(settings[key], str) or len(settings[key]) > 16384):
            raise InstallError(f"one-guard setting {key} must be a string.")
        if key in settings and any(ord(char) < 32 or ord(char) == 127 or 128 <= ord(char) <= 159 for char in settings[key]):
            raise InstallError(f"one-guard setting {key} cannot contain control characters.")
    if settings["mode"] not in ("yes_no", "probability"):
        raise InstallError("one-guard mode must be yes_no or probability.")
    for key, low, high in (("timeout", 0.01, 120.0), ("threshold", 0.0, 1.0)):
        if key in settings:
            value = settings[key]
            if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
                raise InstallError(f"one-guard {key} must be a finite number between {low} and {high}.")
    if "allow_insecure_http" in settings and type(settings["allow_insecure_http"]) is not bool:
        raise InstallError("one-guard allow_insecure_http must be a boolean.")
    try:
        endpoint = urlsplit(settings["endpoint"])
        valid = endpoint.scheme in ("http", "https") and endpoint.hostname and endpoint.username is None and endpoint.password is None and not endpoint.fragment and not endpoint.query and endpoint.path == path
        if any(char.isspace() or ord(char) < 32 for char in settings["endpoint"]):
            valid = False
        if endpoint.scheme == "http" and not settings.get("allow_insecure_http", False):
            try:
                valid = valid and ipaddress.ip_address(endpoint.hostname).is_loopback
            except ValueError:
                valid = False
        if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
            valid = False
        if not valid:
            raise ValueError()
    except ValueError as exc:
        raise InstallError(endpoint_error(path, getattr(args, "insecure_override", None) is not None)) from exc
    return config


def warn_hook_budget(config):
    plugins = config["plugins"]
    timeout = plugins["entries"][PLUGIN]["settings"].get("timeout", 5.0)
    try:
        budget = float(plugins.get("hook_callback_timeout", 30))
    except (TypeError, ValueError):
        budget = 30.0
    if not math.isfinite(budget) or budget < 0:
        budget = 30.0
    if budget != 0 and timeout >= budget:
        print(f"Warning: one-guard timeout is {timeout:g}s, but installation will still be bounded "
              f"by the Hermes host hook timeout ({budget:g}s). Set plugins.hook_callback_timeout "
              f">= {timeout + 5:g} seconds (timeout + 5), or appropriately higher for your host. "
              "The installer has not changed this global setting.", file=sys.stderr)


def prepare_config(home, args, token, yaml, clear_token=False):
    config_path = Path(home) / "config.yaml"
    env_path = Path(home) / ".env"
    raw = read_regular(config_path) if config_path.exists() else b""
    config = parse_config(raw, yaml)
    preserve_legacy_plugins(config, home, yaml)
    legacy = submap(submap(submap(config["plugins"], "entries"), PLUGIN), "settings").get("token")
    env_raw = read_regular(env_path) if env_path.exists() else b""
    env_bytes = merge_secret(env_raw, token, legacy, clear=clear_token)
    config = merge_config(config, args)
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True).encode("utf-8"), env_bytes


def release_files(root):
    safe_path(root)
    if not root.is_dir():
        raise InstallError(f"Release plugin missing: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        safe_path(path)
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_dir():
            continue
        if path.name == MARKER:
            raise InstallError("Release must not contain installer ownership metadata.")
        files[relative.as_posix()] = read_regular(path)
    if not {"plugin.yaml", "__init__.py"}.issubset(files):
        raise InstallError("Release plugin requires plugin.yaml and __init__.py.")
    return files


def hashes(files):
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def check_owned(target):
    safe_path(target)
    if not target.exists():
        return
    if not target.is_dir() or not (target / MARKER).exists():
        raise InstallError(f"Refusing unowned plugin directory: {target}")
    try:
        marker = json.loads(read_regular(target / MARKER))
    except (ValueError, UnicodeError) as exc:
        raise InstallError("Invalid installer ownership metadata.") from exc
    current = {}
    for path in target.rglob("*"):
        safe_path(path)
        if path.is_dir():
            continue
        relative = path.relative_to(target)
        if relative.as_posix() == MARKER:
            continue
        if "__pycache__" in relative.parts and path.suffix == ".pyc":
            read_regular(path)
            continue
        current[relative.as_posix()] = read_regular(path)
    if not isinstance(marker, dict) or marker.get("format") != 1 or marker.get("files") != hashes(current):
        raise InstallError("Installed plugin has modified or unowned files; move it aside before updating.")


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def apply_install(home, files, config_bytes, env_bytes):
    plugins = home / "plugins"
    target = plugins / PLUGIN
    backup = Path(tempfile.mkdtemp(prefix=".one-guard-backup-", dir=home))
    stage = Path(tempfile.mkdtemp(prefix=".one-guard-stage-", dir=home))
    moved = False
    installed = False
    written = []
    originals = {}
    try:
        for name, data in (("config.yaml", config_bytes), (".env", env_bytes)):
            path = home / name
            originals[name] = path.exists()
            if originals[name]:
                write_private(backup / name, read_regular(path))
            write_private(backup / ("new-" + name), data)
        for name, data in files.items():
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_private(dest, data)
        write_private(stage / MARKER, json.dumps({"format": 1, "files": hashes(files)}, sort_keys=True).encode())
        if target.exists():
            os.replace(target, backup / PLUGIN)
            moved = True
        os.replace(stage, target)
        installed = True
        for name in (".env", "config.yaml"):
            os.replace(backup / ("new-" + name), home / name)
            written.append(name)
    except BaseException:
        for name in reversed(written):
            if originals[name]:
                os.replace(backup / name, home / name)
            else:
                (home / name).unlink()
        if installed:
            os.replace(target, backup / "failed-install")
        if moved:
            os.replace(backup / PLUGIN, target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return backup


def validate_source(value):
    if not value.strip():
        raise InstallError("Enter an existing Hermes source directory.")
    source = safe_path(Path(value))
    if not is_source(source):
        raise InstallError("Expected a Hermes checkout containing hermes_cli and hermes or pyproject.toml.")
    return source


def validate_home(value):
    if not value.strip():
        raise InstallError("Enter a Hermes data home directory.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InstallError("Paths cannot contain control characters.")
    home = safe_path(Path(value))
    for directory in (home, home / "plugins"):
        safe_path(directory)
        if directory.exists() and not directory.is_dir():
            raise InstallError(f"Expected a directory: {directory}")
    safe_path(home / "config.yaml")
    safe_path(home / ".env")
    return home


def current_settings(home, yaml):
    path = Path(home) / "config.yaml"
    config = parse_config(read_regular(path) if path.exists() else b"", yaml)
    settings = submap(submap(submap(submap(config, "plugins"), "entries"), PLUGIN), "settings")
    return settings


def number_field(value, low, high):
    try:
        result = float(value)
    except ValueError:
        raise InstallError(f"Enter a finite number between {low} and {high}.") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise InstallError(f"Enter a finite number between {low} and {high}.")
    return result


def judge_defaults(args, settings):
    def value(option, key, fallback):
        given = getattr(args, option, None)
        return settings.get(key, fallback) if given is None else given
    return {
        "protocol": value("protocol", "protocol", "chat_completions"),
        "endpoint": value("endpoint", "endpoint", None),
        "model": value("model", "judge_model", ""),
        "mode": value("response_mode", "mode", "yes_no"),
        "timeout": value("timeout", "timeout", 5),
        "threshold": value("threshold", "threshold", 0.5),
    }


def trial_args(**overrides):
    base = {"protocol": None, "endpoint": None, "allow_insecure_http": False,
            "insecure_override": None, "model": None, "timeout": None,
            "threshold": None, "response_mode": None}
    base.update(overrides)
    return argparse.Namespace(**base)


def configure_judge(ui, args, settings):
    defaults = judge_defaults(args, settings)
    ui.header("03 / Judge connection",
              "Point One Guard at a judge you already run. Nothing is installed or contacted here.")
    args.protocol = ui.choose("Judge protocol",
                              [("chat_completions", "Chat completions  ·  /v1/chat/completions"),
                               ("systemone", "System One  ·  /v1/systemone")],
                              1 if defaults["protocol"] == "systemone" else 0)
    path = "/v1/systemone" if args.protocol == "systemone" else "/v1/chat/completions"
    insecure = getattr(args, "insecure_override", None)
    if insecure is None:
        insecure = args.allow_insecure_http or settings.get("allow_insecure_http", False)
    ui.say("HTTPS or a literal loopback address is recommended. Plaintext HTTP on a LAN "
           "sends tool arguments and credentials without encryption.")
    args.insecure_override = ui.choose("Transport policy",
                                       [(False, "Require HTTPS or loopback (recommended)"),
                                        (True, "Allow plaintext HTTP on my trusted network")],
                                       1 if insecure else 0)
    endpoint_default = defaults["endpoint"] or "http://127.0.0.1:8080" + path
    if urlsplit(str(endpoint_default)).path != path:
        endpoint_default = "http://127.0.0.1:8080" + path

    def endpoint(value):
        merge_config({}, trial_args(protocol=args.protocol, endpoint=value,
                                    insecure_override=args.insecure_override))
        return value

    args.endpoint = ui.text("Judge endpoint", endpoint_default, validate=endpoint)
    model_default = defaults["model"]

    def model(value):
        merge_config({}, trial_args(protocol=args.protocol, endpoint=args.endpoint,
                                    insecure_override=args.insecure_override, model=value))
        return value

    ui.say("Leave the model blank when your endpoint selects one by itself.")
    args.model = ui.text("Judge model", model_default, validate=model)
    ui.header("04 / Decision policy",
              "Uncertain, malformed, or unreachable judgments block the tool call.")
    args.response_mode = ui.choose("Response mode",
                                   [("yes_no", "Yes / no verdict  ·  simplest to reason about"),
                                    ("probability", "Risk probability  ·  needs a threshold")],
                                   1 if defaults["mode"] == "probability" else 0)
    args.timeout = ui.text("Timeout in seconds", defaults["timeout"],
                           validate=lambda value: number_field(value, 0.01, 120))
    if args.response_mode == "probability":
        ui.say("A call is allowed only when the risk score is strictly below the threshold.")
        args.threshold = ui.text("Risk threshold", defaults["threshold"],
                                 validate=lambda value: number_field(value, 0, 1))
    else:
        args.threshold = None


def configure_auth(ui, token, settings, home):
    ui.header("05 / Authentication",
              "Credentials live in the Hermes .env file, never in YAML, logs, or this review.")
    existing = Path(home) / ".env"
    raw = read_regular(existing) if existing.exists() else b""
    legacy = settings.get("token")
    action = ui.choose("Bearer credential",
                       [("keep", "Keep the current credential"),
                        ("replace", "Set a new credential  (input stays hidden)"),
                        ("clear", "Remove the stored credential")])
    if action == "clear":
        if "ONE_GUARD_TOKEN" in os.environ:
            ui.say("ONE_GUARD_TOKEN is set in this process. Unset it in the Hermes "
                   "environment to drop the runtime override.")
        return None, True
    if action == "replace":
        def credential(value):
            merge_secret(b"", value, None)
            return value
        return ui.text("New bearer credential  (blank removes it)", validate=credential, secret=True), False
    merge_secret(raw, token, legacy)
    return token, False


def run_wizard(ui, args, source, yaml):
    token = os.environ.get("ONE_GUARD_TOKEN")
    if token:
        ui.secrets.append(token)
    home_default = args.hermes_home or os.environ.get("HERMES_HOME", "~/.hermes")
    home = None
    section = "all"
    clear_token = False
    while True:
        if section in ("all", "home"):
            ui.header("02 / Hermes data home",
                      "The plugin, config.yaml, and .env are written here. Stop Hermes first.")
            home = ui.text("Hermes data home", home_default, validate=validate_home)
            home_default = str(home)
        settings = current_settings(home, yaml)
        if section in ("all", "judge"):
            configure_judge(ui, args, settings)
        if section in ("all", "auth"):
            token, clear_token = configure_auth(ui, token, settings, home)
        if section == "source":
            source = choose_source(ui)
        try:
            serialized, env_bytes = prepare_config(home, args, token, yaml, clear_token=clear_token)
            check_owned(Path(home) / "plugins" / PLUGIN)
        except InstallError as exc:
            ui.say("Cannot install yet: " + str(exc))
            section = ui.choose("What now?",
                                [("cancel", "Cancel — write nothing"),
                                 ("judge", "Change the judge settings"),
                                 ("home", "Change the destination")])
            if section == "cancel":
                raise Cancelled("Installation cancelled. No files were written.")
            continue
        config = parse_config(serialized, yaml)
        effective = config["plugins"]["entries"][PLUGIN]["settings"]
        from dotenv import dotenv_values
        credential = dotenv_values(stream=io.StringIO(env_bytes.decode("utf-8-sig")),
                                   interpolate=False).get("ONE_GUARD_TOKEN")
        if credential:
            ui.secrets.append(credential)
        ui.header("06 / Review", "Nothing has been written yet. Confirm to install.")
        ui.summary([
            ("Hermes source", source),
            ("Data home", home),
            ("Install to", home / "plugins" / PLUGIN),
            ("Protocol", effective["protocol"]),
            ("Endpoint", effective["endpoint"]),
            ("Model", effective.get("judge_model") or "endpoint default"),
            ("Mode", effective["mode"]),
            ("Timeout", f"{effective.get('timeout', 5):g}s"),
            ("Risk threshold", f"{effective.get('threshold', 0.5):g}"
             if effective["mode"] == "probability" else "not used in yes/no mode"),
            ("Plaintext HTTP", "allowed" if effective.get("allow_insecure_http") else "disabled"),
            ("Credential", "present" if credential else "absent"),
        ])
        if effective.get("prompt"):
            ui.say("Custom prompt: kept as configured (trusted advanced instructions).")
        if any(key.startswith("ONE_GUARD_") and key not in ("ONE_GUARD_TOKEN", "ONE_GUARD_INSTALL_REEXEC")
               for key in os.environ):
            ui.say("Note: ONE_GUARD_* environment overrides are set and take precedence "
                   "over these saved settings at runtime.")
        warn_hook_budget(config)
        action = ui.choose("Install now?",
                           [("install", "Install and enable One Guard"),
                            ("edit", "Change something first"),
                            ("cancel", "Cancel — write nothing")])
        if action == "cancel":
            raise Cancelled("Installation cancelled. No files were written.")
        if action == "install":
            return source, home, token, clear_token
        section = ui.choose("Change what?",
                            [("judge", "Judge connection and decision policy"),
                             ("auth", "Authentication"),
                             ("home", "Hermes data home"),
                             ("source", "Hermes source"),
                             ("review", "Back to the review")])


def parser():
    result = argparse.ArgumentParser(
        description="Install the local one-guard native plugin into a selected Hermes data home. Existing YAML data is preserved; formatting and comments may change. Stop Hermes while installing.",
        epilog="The bearer token comes from ONE_GUARD_TOKEN or an interactive hidden prompt and is saved in Hermes .env (mode 0600). With --yes and no token environment variable, the existing token is preserved or migrated from config.yaml. Defaults: http://127.0.0.1:8080/v1/chat/completions, yes_no mode. No packages or models are downloaded.",
    )
    result.add_argument("--hermes-dir", help="Hermes source checkout (not its data home)")
    result.add_argument("--hermes-home", help="Hermes data home (default: HERMES_HOME or ~/.hermes)")
    result.add_argument("--yes", action="store_true", help="Confirm installation without prompting; ambiguity still requires --hermes-dir")
    ui = result.add_mutually_exclusive_group()
    ui.add_argument("--plain", action="store_true", help="Use the readable numbered wizard")
    ui.add_argument("--wizard", action="store_true", help="Use interactive setup (the default without --yes)")
    result.add_argument("--protocol", choices=("chat_completions", "systemone"), help="Explicit judge protocol (default: existing setting or chat_completions); endpoint path must match")
    result.add_argument("--endpoint", help="Judge URL with the exact selected protocol path")
    result.add_argument("--allow-insecure-http", action="store_true", help="Explicitly permit non-loopback HTTP, including a LAN judge")
    result.add_argument("--model", help="Judge model identifier")
    result.add_argument("--timeout", type=float, help="Judge deadline in seconds, 0.01 through 120 (default: preserve existing setting or runtime default 5)")
    result.add_argument("--threshold", type=float, help="Risk threshold, 0 through 1 (default: preserve existing setting or runtime default 0.5)")
    result.add_argument("--response-mode", choices=("yes_no", "probability"))
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(argv)
    ui = None
    token = os.environ.get("ONE_GUARD_TOKEN")
    clear_token = False
    try:
        if args.yes and args.wizard:
            raise InstallError("Choose either --yes automation or --wizard interactive setup.")
        if args.yes:
            source = select_source(args.hermes_dir, noninteractive=True)
            yaml = load_yaml_module(source, argv)
            home = safe_path(Path(args.hermes_home or os.environ.get("HERMES_HOME", "~/.hermes")))
        else:
            ui = installer_ui.create_ui(args)
            ui.header("ONE GUARD", "A native Hermes tool guard · Configure, review, then install · Ctrl-C cancels")
            source = select_source(args.hermes_dir, ui=ui)
            yaml = load_yaml_module(source, argv, prefer_ui=installer_ui.wants_rich(args))
            source, home, token, clear_token = run_wizard(ui, args, source, yaml)
        for directory in (home, home / "plugins"):
            safe_path(directory)
            if directory.exists() and not directory.is_dir():
                raise InstallError(f"Expected a directory: {directory}")
        config_path = safe_path(home / "config.yaml")
        safe_path(home / ".env")
        target = safe_path(home / "plugins" / PLUGIN)
        files = release_files(RELEASE)
        import fcntl
        prepare_config(home, args, token, yaml, clear_token=clear_token)
        check_owned(target)
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = safe_path(home / ".one-guard-install.lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "r+") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise InstallError("Unsafe installer lock file.")
            fcntl.flock(stream, fcntl.LOCK_EX)
            check_owned(target)
            serialized, env_bytes = prepare_config(home, args, token, yaml, clear_token=clear_token)
            safe_path(home / "plugins").mkdir(exist_ok=True, mode=0o700)
            backup = apply_install(home, files, serialized, env_bytes)
        for line in (f"Installed {target}", f"Enabled in {config_path}", f"Backup: {backup}",
                     "Restart Hermes to load one-guard."):
            print(ui.clean(line) if ui else installer_ui.safe_text(line))
        if args.yes:
            warn_hook_budget(parse_config(serialized, yaml))
        return 0
    except Cancelled as exc:
        print("Error: " + installer_ui.safe_text(str(exc)), file=sys.stderr)
        return 1
    except InstallError as exc:
        message = str(exc)
        if isinstance(token, str) and token:
            message = message.replace(token, "[credential]")
        print("Error: " + (ui.clean(message) if ui else installer_ui.safe_text(message)), file=sys.stderr)
        return 1
    except (ValueError, OSError, EOFError) as exc:
        message = "Input ended before installation was confirmed." if isinstance(exc, EOFError) else str(exc)
        if isinstance(token, str) and token:
            message = message.replace(token, "[credential]")
        print("Error: " + (ui.clean(message) if ui else installer_ui.safe_text(message)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: Installation interrupted. No files were written.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
