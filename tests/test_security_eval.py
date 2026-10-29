import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("security_eval", ROOT / "scripts/eval-security.py")
evaluation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluation
spec.loader.exec_module(evaluation)


class SecurityEvaluationTests(unittest.TestCase):
    def test_corpus_balance_and_shape(self):
        cases = evaluation.load_cases()
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({case["id"] for case in cases}), 100)
        for category in (f"SEC{i:02d}" for i in range(1, 11)):
            group = [case for case in cases if case["category"] == category]
            self.assertEqual([sum(case["label"] == label for case in group)
                              for label in ("risky", "benign")], [5, 5])
            self.assertTrue(all(case["provenance"] == "original synthetic"
                                and isinstance(case["args"], dict) and case["args"] for case in group))

    def test_evaluator_defaults_match_the_plugin_defaults(self):
        """The evaluator must read the same default band as the plugin it evaluates."""
        guard = evaluation.load_guard()
        self.assertEqual(evaluation.DEFAULT_APPROVAL_THRESHOLD,
                         guard.DEFAULTS["approval_threshold"])
        self.assertEqual(evaluation.DEFAULT_BLOCK_THRESHOLD, guard.DEFAULTS["threshold"])

    def test_default_skips_without_loading_transport(self):
        with mock.patch.object(evaluation, "load_guard", side_effect=AssertionError("network path")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(evaluation.main([]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "skipped")

    def test_real_payload_and_stub_exchange(self):
        guard = evaluation.load_guard()
        config = guard.Config(protocol="systemone", endpoint="http://127.0.0.1:8080/v1/systemone",
                              model="test", mode="probability", timeout=2).validate()
        case = evaluation.load_cases()[0]
        def exchange(config, payload, deadline):
            body = json.loads(payload)
            self.assertEqual(body["state"], {"tool_name": case["tool_name"], "args": case["args"]})
            return guard.Outcome(guard.Decision.APPROVE, 0.5)
        with mock.patch.object(guard, "_exchange", side_effect=exchange):
            result = evaluation.evaluate(guard, config, case)
        self.assertEqual((result["decision"], result["score"], result["status"]), ("approve", 0.5, "ok"))
        with mock.patch.object(guard, "_exchange", side_effect=ValueError("secret")):
            result = evaluation.evaluate(guard, config, case)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret", json.dumps(result).lower().split('"error"')[-1])

    def test_payload_contains_only_proposal_for_both_protocols(self):
        guard = evaluation.load_guard()
        case = evaluation.load_cases()[0]
        for protocol, path in (("systemone", "systemone"), ("chat_completions", "chat/completions")):
            config = guard.Config(protocol=protocol, endpoint=f"http://127.0.0.1:8080/v1/{path}",
                                  model="test", mode="probability").validate()
            def exchange(config, payload, deadline):
                body = json.loads(payload)
                proposal = (body["state"] if protocol == "systemone"
                            else json.loads(body["messages"][1]["content"]))
                self.assertEqual(proposal, {"tool_name": case["tool_name"], "args": case["args"]})
                return guard.Outcome(guard.Decision.DENY, 0.7)
            with self.subTest(protocol=protocol), mock.patch.object(guard, "_exchange", side_effect=exchange):
                self.assertEqual(evaluation.evaluate(guard, config, case)["status"], "ok")

    def test_bands_and_error_denominators(self):
        guard = evaluation.load_guard()
        results = []
        for label in ("risky", "benign"):
            for score in (0.49, 0.5, 0.7):
                outcome = guard.parse_verdict(str(score), "probability", 0.5, 0.7)
                results.append({"label": label, "status": "ok", "decision": outcome.decision.value})
            results.append({"label": label, "status": "error", "decision": None})
        metrics = evaluation.metrics(results)
        for key in ("tp_block", "fn_allow", "risky_review", "tn_allow", "fp_block", "fp_review"):
            self.assertEqual(metrics[key], 1)
        self.assertEqual(metrics["errors"], 2)
        self.assertEqual(metrics["fp_block_rate"], 1 / 3)
        self.assertEqual(metrics["fp_review_rate"], 1 / 3)
        self.assertEqual(metrics["hard_block_recall"], 1 / 3)
        self.assertEqual(metrics["block_or_review_recall"], 2 / 3)
        self.assertIsNone(evaluation.metrics([])["fp_block_rate"])
        self.assertIsNone(evaluation.metrics([])["hard_block_recall"])
        self.assertIsNone(evaluation.metrics([])["block_or_review_recall"])

    def test_selection_and_per_category_metrics(self):
        cases = evaluation.load_cases()
        self.assertEqual([c["id"] for c in evaluation.select_cases(cases, "SEC02", 2)],
                         ["SEC02-R1", "SEC02-R2"])
        guard = evaluation.load_guard()
        with mock.patch.object(evaluation, "load_guard", return_value=guard), \
             mock.patch.object(guard, "_exchange", return_value=guard.Outcome(guard.Decision.APPROVE, 0.5)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = evaluation.main(["--run-live", "--endpoint", "http://127.0.0.1:8080/v1/systemone",
                                      "--model", "test", "--category", "SEC02", "--limit", "6"])
        self.assertEqual(status, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(len(data["cases"]), 6)
        self.assertEqual(data["metrics"], data["per_category"]["SEC02"])
        self.assertEqual(data["metrics"]["risky_review"], 5)
        self.assertEqual(data["metrics"]["fp_review"], 1)
        self.assertEqual(data["metrics"]["hard_block_recall"], 0)
        self.assertEqual(data["metrics"]["block_or_review_recall"], 1)

    def test_thresholds_are_configurable_and_validated(self):
        guard = evaluation.load_guard()
        with mock.patch.object(evaluation, "load_guard", return_value=guard), \
             mock.patch.object(guard, "_exchange", return_value=guard.Outcome(guard.Decision.APPROVE, 0.4)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = evaluation.main(["--run-live", "--endpoint", "http://127.0.0.1:8080/v1/systemone",
                                      "--model", "test", "--category", "SEC01", "--limit", "1",
                                      "--approval-threshold", "0.3", "--block-threshold", "0.6"])
        self.assertEqual(status, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["approval_threshold"], 0.3)
        self.assertEqual(data["block_threshold"], 0.6)
        self.assertEqual(data["cases"][0]["decision"], "approve")
        for flags in (["--approval-threshold", "1.5"], ["--block-threshold", "nan"],
                      ["--approval-threshold", "0.8", "--block-threshold", "0.6"]):
            with self.subTest(flags=flags):
                with self.assertRaises(SystemExit) as exit_code:
                    with contextlib.redirect_stderr(io.StringIO()):
                        evaluation.main(flags)
                self.assertEqual(exit_code.exception.code, 2)

    def test_per_category_excludes_errors_and_is_not_pooled(self):
        guard = evaluation.load_guard()
        outcomes = iter([guard.Outcome(guard.Decision.DENY, 0.7), ValueError("bad"),
                         guard.Outcome(guard.Decision.ALLOW, 0.1)])
        def exchange(*args):
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result
        cases = [evaluation.load_cases()[0], evaluation.load_cases()[1], evaluation.load_cases()[10]]
        with mock.patch.object(evaluation, "load_cases", return_value=cases), \
             mock.patch.object(evaluation, "load_guard", return_value=guard), \
             mock.patch.object(guard, "_exchange", side_effect=exchange), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = evaluation.main(["--run-live", "--endpoint", "http://127.0.0.1:8080/v1/systemone",
                                      "--model", "test"])
        self.assertEqual(status, 1)
        groups = json.loads(output.getvalue())["per_category"]
        self.assertEqual(groups["SEC01"]["errors"], 1)
        self.assertEqual(groups["SEC01"]["hard_block_recall"], 1)
        self.assertEqual(groups["SEC02"]["hard_block_recall"], 0)
        self.assertIsNone(groups["SEC02"]["fp_review_rate"])


if __name__ == "__main__":
    unittest.main()
