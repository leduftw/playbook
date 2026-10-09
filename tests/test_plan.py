import tempfile
import unittest
from pathlib import Path

from playbook.ci.plan import check_matrix, release_mode
from playbook.util import Failure
from tests.fixtures import DBIRD, LOCAL, config


class ReleaseModeTest(unittest.TestCase):
    def test_unreleased_project_does_nothing(self):
        self.assertEqual(release_mode("pull_request", "refs/pull/1/merge", "0.0.0", []), "none")

    def test_first_release_verifies_on_a_pr_and_publishes_on_main(self):
        self.assertEqual(release_mode("pull_request", "refs/pull/1/merge", "1.0.0", []), "verify")
        self.assertEqual(release_mode("push", "refs/heads/main", "1.0.0", []), "publish")

    def test_first_release_above_one_point_zero_fails(self):
        with self.assertRaises(Failure) as caught:
            release_mode("pull_request", "refs/pull/1/merge", "2.0.0", [])
        self.assertIn("first release must be 1.0.0", str(caught.exception))

    def test_released_version_does_nothing(self):
        self.assertEqual(release_mode("push", "refs/heads/main", "1.2.0", ["v1.2.0", "v1"]), "none")

    def test_gaps_fail(self):
        with self.assertRaises(Failure):
            release_mode("pull_request", "refs/pull/1/merge", "1.4.0", ["v1.2.0"])

    def test_going_back_to_unreleased_fails(self):
        with self.assertRaises(Failure):
            release_mode("push", "refs/heads/main", "0.0.0", ["v1.0.0"])

    def test_dispatch_off_main_only_verifies(self):
        self.assertEqual(release_mode("workflow_dispatch", "refs/heads/feature", "1.1.0", ["v1.0.0"]), "verify")


class CheckMatrixTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_format_and_lint_run_once_and_tests_everywhere(self):
        (self.root / "leaderboard").mkdir()
        entries = check_matrix(self.root, config(DBIRD))
        rust = [e for e in entries if e["stack"] == "rust"]
        self.assertEqual([e["os"] for e in rust], ["linux", "macos", "windows"])
        self.assertIn("cargo fmt", rust[0]["script"])
        self.assertNotIn("cargo fmt", rust[1]["script"])
        self.assertIn("cargo test", rust[2]["script"])
        self.assertEqual(rust[0]["rust_components"], "rustfmt, clippy")
        self.assertEqual(rust[1]["rust_components"], "")
        node = [e for e in entries if e["stack"] == "node"][0]
        self.assertIn("format (no formatter configured)", node["skipped"])

    def test_local_repo_runs_on_linux_only(self):
        (self.root / "tests").mkdir()
        entries = check_matrix(self.root, config(LOCAL))
        self.assertEqual([e["runner"] for e in entries], ["ubuntu-24.04"])


if __name__ == "__main__":
    unittest.main()
