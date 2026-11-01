import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook.sync import sync_repo

URL = "https://github.com/o/r/pull/7"


class SyncRepoTest(unittest.TestCase):
    def test_the_pr_has_a_capital_and_lands_as_a_lowercase_commit(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch("playbook.sync.clone", return_value=Path(temp)),
            mock.patch("playbook.sync.sync_here", return_value=["AGENTS.md"]),
            mock.patch("playbook.sync.version", return_value="1.4.0"),
            mock.patch("playbook.sync.gh_json", return_value=[]),
            mock.patch("playbook.sync.git") as git,
            mock.patch("playbook.sync.gh", return_value=URL) as gh,
            mock.patch("playbook.sync.has_required_checks", return_value=True),
            mock.patch("playbook.sync.run", return_value=subprocess.CompletedProcess([], 0)) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            sync_repo("o/r")
        git.assert_any_call("commit", "--quiet", "-m", "update playbook files to 1.4.0", cwd=Path(temp))
        created = gh.call_args.args
        self.assertEqual(created[created.index("--title") + 1], "Update playbook files to 1.4.0")
        run.assert_called_with(
            ["gh", "pr", "merge", URL, "--auto", "--squash", "--subject", "update playbook files to 1.4.0 (#7)"],
            check=False,
            capture=True,
        )


if __name__ == "__main__":
    unittest.main()
