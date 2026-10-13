import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
MOCK_CURL = """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
url = next((arg for arg in args if arg.startswith("https://")), "")
method = args[args.index("--request") + 1] if "--request" in args else "GET"
body = args[args.index("--data") + 1] if "--data" in args else ""
max_time = int(args[args.index("--max-time") + 1]) if "--max-time" in args else None
headers = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "--header"]
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(json.dumps({
        "method": method, "url": url, "body": body,
        "max_time": max_time,
        "idempotency": next((h for h in headers if h.startswith("Idempotency-Key: ")), ""),
    }) + "\\n")
if method == "POST" and url.endswith("/v1/actions"):
    marker = os.environ.get("MOCK_CREATE_RETRY_MARKER")
    if marker and not os.path.exists(marker):
        open(marker, "w").close()
        sys.exit(28)  # The server handled the request, but its reply was lost.
    if os.environ.get("MOCK_CREATE_ALWAYS_FAIL"):
        sys.exit(28)
    print(json.dumps({"data": {"id": "act_test", "action_version": 1, "fingerprint": "sha256:" + "a" * 64}}))
elif method == "POST" and url.endswith("/cancel"):
    print(json.dumps({"data": {"status": "cancelled"}}))
else:
    mode = os.environ.get("MOCK_MODE", "approve")
    if mode == "slow_open":
        import time
        time.sleep(max_time)
    status = "expired" if mode == "expired" else "open" if mode in ("open", "slow_open") else "resolved"
    print(json.dumps({"data": {
        "status": status,
        "resolution_option_id": "reject" if mode == "reject" else "approve",
        "resolved_by_type": "system" if mode == "system" else "user",
        "action_version": 2 if mode == "changed" else 1,
        "fingerprint": "sha256:" + ("b" if mode == "changed_fingerprint" else "a") * 64,
    }}))
"""


class ApprovalActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        binary = self.directory / "bin"
        binary.mkdir()
        mock_curl = binary / "curl"
        mock_curl.write_text(MOCK_CURL)
        mock_curl.chmod(0o755)
        self.output = self.directory / "output"
        self.log = self.directory / "calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "MOCK_LOG": str(self.log),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_ACTION_PATH": str(ROOT),
            "GITHUB_REPOSITORY": "example/site",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "deploy",
            "GITHUB_ACTION": "approval",
            "GITHUB_REF_NAME": "main",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_WORKFLOW": "Deploy",
            "GITHUB_SERVER_URL": "https://github.com",
            "ACTIONBOX_TOKEN": "test-source-key",
            "ACTIONBOX_TITLE": "Approve deploy",
            "ACTIONBOX_DESCRIPTION": "Checks passed",
            "ACTIONBOX_CONTEXT_JSON": '[{"type":"key_value","items":{"commit":"abc"}}]',
            "ACTIONBOX_ACTION_ID": "act_test",
            "ACTIONBOX_ACTION_VERSION": "1",
            "ACTIONBOX_FINGERPRINT": "sha256:" + "a" * 64,
            "ACTIONBOX_TIMEOUT": "10",
        }

    def run_script(self, name, **changes):
        return subprocess.run(
            ["bash", str(ROOT / "scripts" / name)],
            env={**self.env, **changes},
            text=True,
            capture_output=True,
            check=False,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_metadata_uses_only_local_scripts_and_no_configurable_api_origin(self):
        metadata = (ROOT / "action.yml").read_text()
        self.assertIn("using: composite", metadata)
        self.assertIn('bash "$GITHUB_ACTION_PATH/scripts/create.sh"', metadata)
        self.assertIn('bash "$GITHUB_ACTION_PATH/scripts/wait.sh"', metadata)
        self.assertNotIn("api-url:", metadata)

    def test_create_request_and_snapshot(self):
        result = self.run_script("create.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.output.read_text().splitlines(),
            ["action_id=act_test", "action_version=1", "fingerprint=sha256:" + "a" * 64],
        )
        call = self.calls()[0]
        self.assertEqual(call["url"], "https://api.actionbox.cloud/v1/actions")
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["idempotency"].startswith("Idempotency-Key: github-"))
        body = json.loads(call["body"])
        self.assertEqual(body["options"][0]["id"], "approve")
        self.assertEqual(body["options"][1]["id"], "reject")
        self.assertTrue(body["expires_at"].endswith("Z"))
        self.assertEqual(body["context"][0]["items"]["commit"], "abc")
        self.assertEqual(body["metadata"]["origin"]["url"], "https://github.com/example/site/actions/runs/123")
        self.assertNotIn("test-source-key", result.stdout + result.stderr + self.output.read_text())

    def test_separate_invocations_have_distinct_idempotency_keys(self):
        self.assertEqual(self.run_script("create.sh").returncode, 0)
        self.assertEqual(self.run_script("create.sh").returncode, 0)
        self.assertNotEqual(self.calls()[0]["idempotency"], self.calls()[1]["idempotency"])

    def test_create_retries_lost_reply_with_identical_key_and_payload(self):
        marker = self.directory / "first_create_lost"
        result = self.run_script("create.sh", MOCK_CREATE_RETRY_MARKER=str(marker))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["idempotency"], calls[1]["idempotency"])
        self.assertEqual(calls[0]["body"], calls[1]["body"])
        self.assertIn("action_id=act_test", self.output.read_text())

    def test_create_fails_closed_after_retry_exhaustion(self):
        result = self.run_script("create.sh", MOCK_CREATE_ALWAYS_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertFalse(self.output.exists())

    def test_invalid_context_is_rejected_before_api_call(self):
        result = self.run_script("create.sh", ACTIONBOX_CONTEXT_JSON='{"not":"an array"}')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_long_github_names_fit_actionbox_origin_limits(self):
        result = self.run_script(
            "create.sh",
            GITHUB_REPOSITORY="o" * 39 + "/" + "r" * 100,
            GITHUB_REF_NAME="branch-" + "x" * 248,
            GITHUB_WORKFLOW="Review " + "w" * 250,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        origin = json.loads(self.calls()[0]["body"])["metadata"]["origin"]
        self.assertLessEqual(len(origin["ref"]), 240)
        self.assertIn("run 123", origin["ref"])
        self.assertTrue(all(len(label) <= 120 for label in origin["labels"]))
        self.assertTrue(origin["url"].endswith("/actions/runs/123"))

    def test_only_unchanged_human_approval_succeeds(self):
        result = self.run_script("wait.sh", MOCK_MODE="approve")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text().splitlines(), ["decision=approve", "status=resolved"])
        self.assertEqual(len(self.calls()), 1)

    def test_all_non_approvals_fail_closed(self):
        for mode in ("reject", "expired", "system", "changed", "changed_fingerprint"):
            with self.subTest(mode=mode):
                self.output.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = self.run_script("wait.sh", MOCK_MODE=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertEqual(len(self.calls()), 1)

    def test_timeout_cancels_pending_action(self):
        start = time.monotonic()
        result = self.run_script("wait.sh", MOCK_MODE="slow_open", ACTIONBOX_TIMEOUT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - start, 3)
        polls = [call for call in self.calls() if "wait_seconds=" in call["url"]]
        self.assertTrue(polls)
        self.assertTrue(all(call["max_time"] <= 1 and "wait_seconds=0" in call["url"] for call in polls))
        self.assertTrue(any(call["url"].endswith("/act_test/cancel") for call in self.calls()))

    def test_invalid_timeout_is_rejected(self):
        for value in ("0", "21601", "abc", "100000000000000"):
            with self.subTest(value=value):
                result = self.run_script("wait.sh", ACTIONBOX_TIMEOUT=value)
                self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
