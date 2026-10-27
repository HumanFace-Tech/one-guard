import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SmokeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, payload))
        state = payload["state"]
        safe = state["args"].get("command") == "pwd" or state["tool_name"] == "write_file"
        if payload["questions"]["risk"]["type"] == "noul":
            answer = {"type": "noul", "noul": .1 if safe else .9}
        else:
            answer = {"type": "choice", "choice": "no" if safe else "yes",
                      "probabilities": {"yes": .1 if safe else .9, "no": .9 if safe else .1},
                      "confidence": .8}
        body = json.dumps({"model": "fixture", "answers": {"risk": answer},
                           "usage": {"input_tokens": 42, "output_tokens": 0}}).encode()
        if self.server.invalid:
            body = b'{}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SmokeTests(unittest.TestCase):
    def test_live_flag_and_synthetic_only_round_trips(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
        server.requests, server.invalid = [], False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        command = [sys.executable, str(ROOT / "scripts/smoke-systemone.py"),
                   "--endpoint", f"http://127.0.0.1:{server.server_port}/v1/systemone", "--model", "fixture"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--run-live is required", result.stderr)
            self.assertEqual(server.requests, [])
            result = subprocess.run([*command, "--run-live", "--timeout", "121"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Invalid judge configuration", result.stderr)
            self.assertEqual(server.requests, [])
            for mode in ("yes_no", "probability"):
                result = subprocess.run([*command, "--run-live", "--response-mode", mode, "--timeout", "120"],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.count("PASS"), 6)
            self.assertEqual(len(server.requests), 12)
            self.assertTrue(all(path == "/v1/systemone" for path, _ in server.requests))
            server.invalid = True
            result = subprocess.run([*command, "--run-live"], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout.count("unavailable/invalid response"), 6)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
