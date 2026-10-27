import contextlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def ui_module(name: str) -> bool:
    return any(name == package or name.startswith(package + ".")
               for package in ("prompt_toolkit", "wcwidth"))


def run(argv: list[str], env: dict[str, str], timeout: int, capture: bool = False) -> str:
    process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, argv)
    return output.decode("utf-8").strip() if capture else ""


@contextlib.contextmanager
def provision():
    with tempfile.TemporaryDirectory(prefix="one-guard-ui-") as directory:
        root = Path(directory).resolve()
        env = {"HOME": str(root), "PATH": os.defpath, "PIP_CONFIG_FILE": os.devnull,
               "PYTHONNOUSERSITE": "1"}
        python = root / "venv" / "bin" / "python"
        purelib = None
        saved_modules = None
        path_added = False
        try:
            run([sys.executable, "-I", "-m", "venv", str(root / "venv")], env, 45)
            run([str(python), "-I", "-m", "pip", "--require-virtualenv",
                 "--disable-pip-version-check", "--no-input", "--no-cache-dir", "install",
                 "--index-url", "https://pypi.org/simple", "--only-binary=:all:",
                 "--require-hashes", "--no-deps", "--timeout", "5", "--retries", "0",
                 "-r", str(Path(__file__).resolve().parent / "installer-ui-requirements.txt")], env, 45)
            location = run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], env, 10, capture=True)
            path = Path(location).resolve(strict=True)
            if not path.is_dir() or root / "venv" not in path.parents:
                raise ValueError("Invalid temporary UI package path")
            purelib = str(path)
            saved_modules = {name: module for name, module in sys.modules.copy().items()
                             if ui_module(name)}
            for name in saved_modules:
                del sys.modules[name]
            sys.path.insert(0, purelib)
            path_added = True
            yield True
        finally:
            if path_added:
                sys.path.remove(purelib)
            if saved_modules is not None:
                for name in list(sys.modules):
                    if ui_module(name):
                        del sys.modules[name]
                sys.modules.update(saved_modules)
