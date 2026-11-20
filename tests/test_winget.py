import contextlib
import hashlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from playbook.ci import winget
from playbook.ci.winget import architectures, layout_problem, manifest_path, pull_request
from playbook.util import Failure
from tests import fixtures
from tests.fixtures import SUBTITLER, config

OLD_DBIRD = """PackageIdentifier: leduftw.dbird
InstallerType: zip
NestedInstallerType: portable
NestedInstallerFiles:
- RelativeFilePath: dbird-v1.2.0-x86_64-pc-windows-msvc\\\\dbird.exe
  PortableCommandAlias: dbird
Installers:
- Architecture: x64
  InstallerUrl: https://example.com/dbird.zip
"""

MONAD = """NestedInstallerFiles:
- RelativeFilePath: monad.exe
  PortableCommandAlias: monad
Installers:
- Architecture: x64
- Architecture: arm64
"""


class WingetTest(unittest.TestCase):
    def test_manifest_path(self):
        self.assertEqual(manifest_path("leduftw.dbird"), "manifests/l/leduftw/dbird")

    def test_architectures(self):
        self.assertEqual(architectures(OLD_DBIRD), ["x64"])
        self.assertEqual(architectures(MONAD), ["arm64", "x64"])

    def test_a_manifest_matching_the_archive_layout_passes(self):
        self.assertIsNone(layout_problem(MONAD, "monad"))

    def test_an_old_archive_layout_is_caught_before_publishing(self):
        problem = layout_problem(OLD_DBIRD, "dbird")
        self.assertIn("dbird-v1.2.0-x86_64-pc-windows-msvc", problem)
        self.assertIn("RelativeFilePath: dbird.exe", problem)

    def test_finds_the_pull_request_wingetcreate_opened(self):
        output = "Submitting pull request for manifest...\nPull request can be found here: https://github.com/microsoft/winget-pkgs/pull/452784\n"
        self.assertEqual(pull_request(output), "https://github.com/microsoft/winget-pkgs/pull/452784")
        self.assertIsNone(pull_request("Submitting pull request for manifest...\n"))


class FirstReleaseTest(unittest.TestCase):
    """A package can't be on WinGet before its first release exists."""

    def setUp(self):
        self.config = config(SUBTITLER)
        patcher = mock.patch.object(winget, "newest_installer_manifest", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_first_release_only_warns_on_the_release_pr(self):
        with mock.patch.object(winget, "warn") as warn:
            winget.check(self.config, "1.0.0", "leduftw/subtitler")
        self.assertIn("wingetcreate new", warn.call_args.args[0])

    def test_a_later_release_of_a_missing_package_still_fails(self):
        with self.assertRaisesRegex(Failure, "isn't in winget-pkgs yet"):
            winget.check(self.config, "1.0.1", "leduftw/subtitler")

    def test_a_first_release_says_exactly_what_to_submit(self):
        with (
            mock.patch.dict(winget.os.environ, {"WINGET_TOKEN": "token"}),
            mock.patch.object(winget, "repository", return_value="leduftw/subtitler"),
            mock.patch.object(winget, "warn"),
            mock.patch.object(winget, "summary") as summary,
        ):
            winget.submit(Path("."), self.config, "1.0.0")
        message = summary.call_args.args[0]
        self.assertIn("/v1.0.0/subtitler-windows-x64.zip", message)
        self.assertIn("/v1.0.0/subtitler-windows-arm64.zip", message)

    def test_a_later_submission_of_a_missing_package_still_fails(self):
        with (
            mock.patch.dict(winget.os.environ, {"WINGET_TOKEN": "token"}),
            self.assertRaisesRegex(Failure, "submit the first version by hand"),
        ):
            winget.submit(Path("."), self.config, "1.0.1")


def gh_error(message, status):
    """What `gh api` gives back when GitHub answers with an error: (exit code, stdout, stderr)."""
    return 1, json.dumps({"message": message, "status": str(status)}), f"gh: {message} (HTTP {status})\n"


def merged(merge_type):
    return 0, json.dumps({"message": "...", "merge_type": merge_type, "base_branch": "microsoft:master"}), ""


OWNER = (0, "leduftw\n", "")
FORK = (0, "master\n", "")
NO_WORKFLOW_SCOPE = gh_error(
    "refusing to allow a Personal Access Token to create or update workflow "
    "`.github/workflows/scriptAnalyzer.yaml` without `workflow` scope",
    422,
)
SYNC_BY_HAND = (
    "sync the fork by hand (gh repo sync leduftw/winget-pkgs --branch master), "
    "then run: gh workflow run playbook.yml --ref main --repo leduftw/monad"
)


class SyncForkTest(unittest.TestCase):
    """WingetCreate submits from the token owner's fork, so the fork is brought up to date first."""

    def sync(self, *responses):
        """Run sync_fork with each gh call answering the next response; return what it said."""
        self.calls, queue = [], list(responses)

        def fake_run(command, check=True, capture=False, env=None, input=None, cwd=None):
            self.calls.append(command)
            self.assertEqual(env, {"GH_TOKEN": "winget-token"})
            return subprocess.CompletedProcess(command, *queue.pop(0))

        with mock.patch.object(winget, "run", fake_run), mock.patch.object(winget, "say") as say:
            winget.sync_fork("winget-token", "leduftw/monad")
        self.assertEqual(queue, [])
        return [call.args[0] for call in say.call_args_list]

    def test_the_tokens_owner_syncs_their_fork_and_says_so(self):
        said = self.sync(OWNER, FORK, merged("fast-forward"))
        self.assertEqual(
            self.calls,
            [
                ["gh", "api", "user", "--jq", ".login"],
                ["gh", "api", "repos/leduftw/winget-pkgs", "--jq", ".default_branch"],
                ["gh", "api", "--method", "POST", "repos/leduftw/winget-pkgs/merge-upstream", "-f", "branch=master"],
            ],
        )
        self.assertEqual(said, ["synced leduftw/winget-pkgs with microsoft/winget-pkgs (fast-forward)"])

    def test_a_fork_already_in_sync_says_so(self):
        said = self.sync(OWNER, FORK, merged("none"))
        self.assertEqual(said, ["leduftw/winget-pkgs was already in sync with microsoft/winget-pkgs"])

    def test_a_missing_fork_is_left_for_wingetcreate_to_create(self):
        said = self.sync(OWNER, gh_error("Not Found", 404))
        self.assertEqual(len(self.calls), 2)
        self.assertIn("WingetCreate creates it", said[0])

    def test_a_workflow_change_upstream_asks_for_the_workflow_scope(self):
        with self.assertRaises(Failure) as caught:
            self.sync(OWNER, FORK, NO_WORKFLOW_SCOPE)
        message = str(caught.exception)
        self.assertTrue(message.startswith("couldn't sync leduftw/winget-pkgs with microsoft/winget-pkgs: refusing"))
        self.assertIn("without `workflow` scope (HTTP 422)", message)
        self.assertIn("Add that scope to WINGET_TOKEN", message)
        self.assertIn(SYNC_BY_HAND, message)

    def test_any_other_refusal_still_says_how_to_sync_by_hand(self):
        with self.assertRaises(Failure) as caught:
            self.sync(OWNER, FORK, gh_error("Server Error", 500))
        message = str(caught.exception)
        self.assertIn("couldn't sync leduftw/winget-pkgs with microsoft/winget-pkgs: Server Error (HTTP 500)", message)
        self.assertIn(f"To submit this release, {SYNC_BY_HAND}", message)
        self.assertNotIn("workflow scope", message)

    def test_a_token_that_cant_sign_in_fails_before_touching_the_fork(self):
        with self.assertRaisesRegex(Failure, r"WINGET_TOKEN can't sign in to GitHub: Bad credentials \(HTTP 401\)"):
            self.sync(gh_error("Bad credentials", 401))
        self.assertEqual(len(self.calls), 1)


class SubmitTest(unittest.TestCase):
    """A release of monad, which is on WinGet, submitted with stand-ins for GitHub and WingetCreate."""

    TOOL = b"wingetcreate.exe"

    def setUp(self):
        self.events = []
        self.enterContext(mock.patch.dict(winget.os.environ, {"WINGET_TOKEN": "winget-token"}))
        self.enterContext(mock.patch.object(winget, "newest_installer_manifest", return_value=MONAD))
        self.enterContext(mock.patch.object(winget, "repository", return_value="leduftw/monad"))
        self.download = self.enterContext(
            mock.patch.object(winget.urllib.request, "urlopen", side_effect=lambda url: io.BytesIO(self.TOOL))
        )
        self.enterContext(mock.patch.object(winget, "WINGETCREATE_SHA256", hashlib.sha256(self.TOOL).hexdigest()))
        self.enterContext(mock.patch.object(winget, "run", self.wingetcreate))
        self.enterContext(mock.patch.object(winget, "say"))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def wingetcreate(self, command, **kwargs):
        self.events.append("wingetcreate")
        return subprocess.CompletedProcess(command, 0, "https://github.com/microsoft/winget-pkgs/pull/1", "")

    def submit(self):
        winget.submit(Path("."), config(fixtures.MONAD), "2.2.4")

    def test_the_fork_is_synced_before_wingetcreate_submits(self):
        with mock.patch.object(winget, "sync_fork", side_effect=lambda *args: self.events.append(("sync", args))):
            self.submit()
        self.assertEqual(self.events, [("sync", ("winget-token", "leduftw/monad")), "wingetcreate"])

    def test_a_failed_sync_stops_before_wingetcreate_is_downloaded(self):
        refusal = Failure("couldn't sync leduftw/winget-pkgs with microsoft/winget-pkgs")
        with (
            mock.patch.object(winget, "sync_fork", side_effect=refusal),
            self.assertRaisesRegex(Failure, "couldn't sync"),
        ):
            self.submit()
        self.download.assert_not_called()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
