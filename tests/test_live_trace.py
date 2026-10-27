import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/live-trace-systemone.py"
spec = importlib.util.spec_from_file_location("live_trace", SCRIPT)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(payload)
        command = payload["state"]["args"]["command"]
        score = self.server.scores.get(command, .9)
        answer = {"type": "noul", "noul": score}
        body = json.dumps({"model": "fixture", "answers": {"risk": answer},
                           "usage": {"input_tokens": 1, "output_tokens": 0}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LiveTraceTests(unittest.TestCase):
    def test_fixtures_and_round_trip(self):
        self.assertEqual(len(live.CASES), 12)
        self.assertIn('PROBE_PAYLOAD_ABC123', live.EXACT)
        self.assertTrue(all(case[2] in live.EXACT for case in live.CASES[1:4]))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.requests = []
        server.scores = {case[2]: .1 for case in live.CASES if case[3] == "benign"}
        # Equality must deny, even for a benign expected label.
        server.scores["pwd"] = .5
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        command = [sys.executable, str(SCRIPT), "--endpoint",
                   f"http://127.0.0.1:{server.server_port}/v1/systemone", "--model", "fixture"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(server.requests, [])
            result = subprocess.run(command + ["--run-live", "--workers", "2"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stderr)
            rows = json.loads(result.stdout)["results"]
            self.assertEqual(len(rows), 12)
            self.assertEqual(len(server.requests), 12)
            pwd = next(row for row in rows if row["case"] == "pwd")
            self.assertEqual((pwd["status"], pwd["verdict"], pwd["score"]), ("FAIL", "deny", .5))
            self.assertTrue(all(req["questions"]["risk"]["type"] == "noul" for req in server.requests))
            self.assertIn(live.EXACT, [req["state"]["args"]["command"] for req in server.requests])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_connection_error_is_unknown(self):
        guard = live.load_guard()
        config = guard.Config(protocol="systemone", endpoint="http://127.0.0.1:1/v1/systemone",
                              mode="probability", timeout=.1).validate()
        result = live.evaluate(guard, config, live.CASES[0])
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["observed"], "unknown")


if __name__ == "__main__":
    unittest.main()
