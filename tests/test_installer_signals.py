import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerSignalTests(unittest.TestCase):
    def test_shield_signal_at_release_boundary(self):
        script = r'''
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
scope = m.SignalScope(); fired = [False]
def trace(frame, event, arg):
    if frame.f_code is m.SignalScope.shield.__wrapped__.__code__ and event == "line" and frame.f_lineno == m.SignalScope.shield.__wrapped__.__code__.co_firstlineno + 6 and not fired[0]:
        fired[0] = True
        os.kill(os.getpid(), signal.SIGTERM)
    return trace
with scope:
    sys.settrace(trace)
    try:
        with scope.shield(): pass
    except m.Interrupted: pass
    else: raise AssertionError("missing cancellation")
    finally: sys.settrace(None)
assert fired[0] and scope.depth == 0
'''
        subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/install.py")], check=True, timeout=10)

    def test_bootstrap_signal_on_communicate_error_reaps_child(self):
        script = r'''
import importlib.util, os, signal, subprocess, sys
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
original = subprocess.Popen; children = []; fired = [False]
def spawn(*args, **kwargs):
    child = original(*args, **kwargs); children.append(child)
    def fail(*args, **kwargs): raise OSError("injected")
    child.communicate = fail
    return child
def trace(frame, event, arg):
    if frame.f_code is m.installer_bootstrap.run.__code__ and event == "exception" and isinstance(arg[1], OSError) and not fired[0]:
        fired[0] = True
        del children[0].communicate
        os.kill(os.getpid(), signal.SIGTERM)
    return trace
m.installer_bootstrap.subprocess.Popen = spawn
with m.signals:
    sys.settrace(trace)
    try:
        m.installer_bootstrap.run([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), 60)
    except OSError as exc: assert str(exc) == "injected"
    else: raise AssertionError("missing original error")
    finally: sys.settrace(None)
assert fired[0] and len(children) == 1 and children[0].returncode is not None
try: os.waitpid(children[0].pid, os.WNOHANG)
except ChildProcessError: pass
else: raise AssertionError("child not reaped")
'''
        subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/install.py")], check=True, timeout=10)

    def test_error_to_rollback_transition_defers_first_and_repeated_signals(self):
        script = r'''
import importlib.util, os, pathlib, signal, sys
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
home = pathlib.Path(sys.argv[2]); home.mkdir(); (home / "plugins").mkdir()
target = home / "plugins" / m.PLUGIN; target.mkdir(); (target / "old").write_text("old")
original = m.os.replace; count = [0]; fired = [False]
def replace(src, dst):
    count[0] += 1
    if count[0] == 2:
        raise OSError("injected")
    original(src, dst)
    if fired[0]:
        os.kill(os.getpid(), signal.SIGTERM)
m.os.replace = replace
def trace(frame, event, arg):
    if frame.f_code is m.apply_install.__code__ and event == "exception" and isinstance(arg[1], OSError):
        fired[0] = True
        os.kill(os.getpid(), signal.SIGTERM)
    return trace
previous = signal.getsignal(signal.SIGTERM)
with m.signals:
    sys.settrace(trace)
    try:
        m.apply_install(home, {"new": b"new"}, b"config", b"env")
    except OSError:
        pass
    else:
        raise AssertionError("missing original error")
    finally:
        sys.settrace(None)
assert fired[0] and (target / "old").read_text() == "old"
assert not list(home.glob(".one-guard-stage-*"))
assert signal.getsignal(signal.SIGTERM) == previous
'''
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/install.py"),
                            str(Path(temp) / "home")], check=True, timeout=10)

    def test_first_install_and_before_rename_cancellation(self):
        script = r'''
import importlib.util, os, pathlib, signal, sys
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
home = pathlib.Path(sys.argv[2]); home.mkdir(); (home / "plugins").mkdir()
original_write = m.write_private; original_replace = m.os.replace
def write(path, data):
    original_write(path, data)
    if sys.argv[3] == "before": os.kill(os.getpid(), signal.SIGTERM)
def replace(src, dst):
    original_replace(src, dst)
    os.kill(os.getpid(), signal.SIGTERM)
m.write_private = write; m.os.replace = replace
with m.signals:
    try: m.apply_install(home, {"new": b"new"}, b"config", b"env")
    except m.Interrupted: pass
    else: raise AssertionError("missing cancellation")
assert not (home / "plugins" / m.PLUGIN).exists()
assert not (home / "config.yaml").exists() and not (home / ".env").exists()
assert not list(home.glob(".one-guard-stage-*"))
'''
        for boundary in ("before", "rename"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temp:
                subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/install.py"),
                                str(Path(temp) / "home"), boundary], check=True, timeout=10)

    def test_bootstrap_signal_from_unblocked_thread_during_spawn_is_reaped(self):
        script = r'''
import importlib.util, os, signal, subprocess, sys, threading
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
original = subprocess.Popen; children = []
def spawn(*args, **kwargs):
    child = original(*args, **kwargs); children.append(child)
    def send():
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        os.kill(os.getpid(), signal.SIGTERM)
    thread = threading.Thread(target=send); thread.start(); thread.join()
    return child
m.installer_bootstrap.subprocess.Popen = spawn
previous = signal.getsignal(signal.SIGTERM)
with m.signals:
    try:
        m.installer_bootstrap.run([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), 60)
    except m.Interrupted: pass
    else: raise AssertionError("missing cancellation")
assert len(children) == 1 and children[0].returncode is not None
try: os.waitpid(children[0].pid, os.WNOHANG)
except ChildProcessError: pass
else: raise AssertionError("child not reaped")
assert signal.getsignal(signal.SIGTERM) == previous
'''
        subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/install.py")],
                       check=True, timeout=10)

    def test_signal_at_each_replace_restores_originals_even_with_repeated_signals(self):
        script = r'''
import importlib.util, os, pathlib, signal, sys
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
home = pathlib.Path(sys.argv[2]); home.mkdir(); (home / "plugins").mkdir()
target = home / "plugins" / m.PLUGIN; target.mkdir(); (target / "old").write_text("old")
(home / "config.yaml").write_text("old-config"); (home / ".env").write_text("old-env")
original = m.os.replace; count = [0]; fired = [False]
def replace(src, dst):
    original(src, dst)
    count[0] += 1
    if count[0] == int(sys.argv[3]) or fired[0]:
        fired[0] = True
        os.kill(os.getpid(), signal.SIGTERM)
m.os.replace = replace
with m.signals:
    try:
        m.apply_install(home, {"new": b"new"}, b"new-config", b"new-env")
    except m.Interrupted:
        pass
    else:
        raise AssertionError("signal not delivered")
assert (target / "old").read_text() == "old"
assert (home / "config.yaml").read_text() == "old-config"
assert (home / ".env").read_text() == "old-env"
assert not list(home.glob(".one-guard-stage-*"))
'''
        for boundary in range(1, 5):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temp:
                subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/install.py"),
                                str(Path(temp) / "home"), str(boundary)], check=True, timeout=10)

    def test_bootstrap_cancellation_kills_and_reaps_child(self):
        script = r'''
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with m.signals:
    try:
        m.installer_bootstrap.run([sys.executable, "-c", "import os,time; print(os.getpid(), flush=True); time.sleep(60)"], dict(os.environ), 60, capture=True)
    except m.Interrupted:
        sys.exit(0)
sys.exit(2)
'''
        process = subprocess.Popen([sys.executable, "-c", script, str(ROOT / "scripts/install.py")])
        try:
            import time
            children = Path(f"/proc/{process.pid}/task/{process.pid}/children")
            deadline = time.monotonic() + 10
            child = None
            while time.monotonic() < deadline:
                if children.exists():
                    ids = children.read_text().split()
                    if ids:
                        child = int(ids[0])
                        break
                time.sleep(0.01)
            self.assertIsNotNone(child)
            os.kill(process.pid, signal.SIGHUP)
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertFalse(Path(f"/proc/{child}").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()
