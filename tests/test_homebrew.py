import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook import stacks
from playbook.ci import homebrew
from playbook.ci.homebrew import class_name, render
from playbook.util import Failure
from tests.fixtures import DBIRD, MONAD, SUBTITLER, config

TARGETS = ("macos-arm64", "macos-x64", "linux-x64", "linux-arm64")


def urls(name):
    return {t: f"https://github.com/leduftw/{name}/releases/download/v1.0.0/{name}-{t}.zip" for t in TARGETS}


DIGESTS = {t: str(i) * 64 for i, t in enumerate(TARGETS)}


class FormulaTest(unittest.TestCase):
    def test_class_names(self):
        self.assertEqual(class_name("dbird"), "Dbird")
        self.assertEqual(class_name("media-library"), "MediaLibrary")

    def test_dbird_formula_covers_both_cpus_on_both_systems(self):
        text = render(config(DBIRD), "1.3.0", "leduftw/dbird", urls("dbird"), DIGESTS)
        self.assertIn('desc "Fully playable terminal recreation of the classic Flappy Bird game"', text)
        self.assertIn('homepage "https://github.com/leduftw/dbird"', text)
        self.assertEqual(text.count("if Hardware::CPU.arm?"), 2)
        self.assertIn('bin.install "dbird"', text)
        self.assertIn('assert_match version.to_s, shell_output("#{bin}/dbird --version")', text)
        self.assertNotIn("depends_on macos", text)

    def test_monad_formula_keeps_its_requirements(self):
        text = render(config(MONAD), "2.0.2", "leduftw/monad", urls("monad"), DIGESTS)
        self.assertIn("depends_on macos: :sonoma", text)
        self.assertIn('odie "monad needs macOS 14.2 or newer" if OS.mac? && MacOS.version < "14.2"', text)
        self.assertIn('depends_on "pulseaudio"', text)
        self.assertIn('bin.install "monad-audiotap" if OS.mac?', text)
        self.assertIn('pkgshare.install "monad.example.json"', text)
        self.assertIn('shell_output("#{bin}/monad version")', text)

    def test_dependencies_can_be_declared_per_system(self):
        text = render(config(SUBTITLER), "1.0.0", "leduftw/subtitler", urls("subtitler"), DIGESTS)
        macos = text.split("on_macos do", 1)[1].split("on_linux do", 1)[0]
        linux = text.split("on_linux do", 1)[1].split("def install", 1)[0]
        self.assertIn('depends_on "ffmpeg"', macos)
        self.assertIn('depends_on "ffmpeg"', linux)

    def test_a_python_program_stays_beside_its_runtime_behind_a_link(self):
        text = render(config(SUBTITLER), "1.2.3", "leduftw/subtitler", urls("subtitler"), DIGESTS)
        self.assertIn('libexec.install "subtitler", "subtitler-runtime-1.2.3"', text)
        self.assertIn('bin.install_symlink libexec/"subtitler"', text)
        self.assertNotIn('bin.install "subtitler"', text)
        self.assertIn('assert_match version.to_s, shell_output("#{bin}/subtitler --version")', text)

    def test_other_programs_still_go_straight_into_bin(self):
        text = render(config(DBIRD), "1.3.0", "leduftw/dbird", urls("dbird"), DIGESTS)
        self.assertNotIn("libexec", text)

    def test_a_linux_dependency_stays_off_macos(self):
        text = render(config(MONAD), "2.0.2", "leduftw/monad", urls("monad"), DIGESTS)
        macos = text.split("on_macos do", 1)[1].split("on_linux do", 1)[0]
        self.assertNotIn("pulseaudio", macos)

    @unittest.skipUnless(shutil.which("ruby"), "Ruby isn't installed")
    def test_formulae_are_valid_ruby(self):
        with tempfile.TemporaryDirectory() as temp:
            for fixture, name in ((DBIRD, "dbird"), (MONAD, "monad"), (SUBTITLER, "subtitler")):
                path = Path(temp) / f"{name}.rb"
                path.write_text(render(config(fixture), "1.0.0", f"leduftw/{name}", urls(name), DIGESTS))
                subprocess.run(["ruby", "-c", str(path)], check=True, capture_output=True)


HEAD, MOVED = "a" * 40, "b" * 40
COMMITTED = json.dumps(
    {"data": {"createCommitOnBranch": {"commit": {"url": "https://github.com/leduftw/homebrew-tap/commit/c"}}}}
)


def formula_at_commit(text):
    blob = {"text": text} if text is not None else None
    return json.dumps({"data": {"repository": {"object": blob}}})


class CommitToTapTest(unittest.TestCase):
    def commit(self, *results, previous="old formula\n"):
        """Run commit_to_tap with each gh call returning the next (exit code, stdout); return the calls."""
        calls, queue = [], list(results)

        def fake_run(command, check=True, capture=False, env=None, input=None, cwd=None):
            calls.append({"command": command, "env": env, "input": input})
            code, stdout = queue.pop(0)
            if check and code:
                raise Failure(f"`{' '.join(command)}` failed")
            return subprocess.CompletedProcess(command, code, stdout, "")

        with mock.patch.object(homebrew, "run", fake_run):
            homebrew.commit_to_tap(
                "Formula/dbird.rb", "class Dbird\n", "update dbird to 1.4.2", HEAD, "app-token", previous
            )
        self.assertEqual(queue, [])
        return calls

    def change(self, call):
        return json.loads(call["input"])["variables"]["input"]

    def test_the_app_commits_the_formula_through_the_api(self):
        calls = self.commit((0, COMMITTED))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["command"], ["gh", "api", "graphql", "--input", "-"])
        self.assertEqual(calls[0]["env"], {"GH_TOKEN": "app-token"})
        change = self.change(calls[0])
        self.assertEqual(change["branch"], {"repositoryNameWithOwner": "leduftw/homebrew-tap", "branchName": "main"})
        self.assertEqual(change["expectedHeadOid"], HEAD)
        self.assertEqual(change["message"], {"headline": "update dbird to 1.4.2"})
        [addition] = change["fileChanges"]["additions"]
        self.assertEqual(addition["path"], "Formula/dbird.rb")
        self.assertEqual(base64.b64decode(addition["contents"]).decode(), "class Dbird\n")

    def test_the_commit_names_no_author_so_github_signs_it(self):
        change = self.change(self.commit((0, COMMITTED))[0])
        self.assertEqual(set(change), {"branch", "message", "expectedHeadOid", "fileChanges"})

    def test_a_tap_that_moved_gets_the_commit_on_top(self):
        calls = self.commit(
            (1, '{"errors": [{"message": "Expected branch to point to a"}]}'),
            (0, MOVED + "\n"),
            (0, formula_at_commit("old formula\n")),
            (0, COMMITTED),
        )
        self.assertEqual(
            calls[1]["command"], ["gh", "api", "repos/leduftw/homebrew-tap/git/ref/heads/main", "--jq", ".object.sha"]
        )
        lookup = json.loads(calls[2]["input"])["variables"]
        self.assertEqual(
            lookup, {"owner": "leduftw", "name": "homebrew-tap", "expression": f"{MOVED}:Formula/dbird.rb"}
        )
        self.assertEqual(calls[2]["env"], {"GH_TOKEN": "app-token"})
        self.assertEqual(self.change(calls[3])["expectedHeadOid"], MOVED)

    def test_an_applied_commit_with_a_lost_response_is_not_repeated(self):
        calls = self.commit((1, "connection lost"), (0, MOVED), (0, formula_at_commit("class Dbird\n")))
        self.assertEqual(len(calls), 3)

    def test_a_concurrent_change_to_the_same_formula_is_not_overwritten(self):
        with self.assertRaisesRegex(Failure, "refusing to overwrite its concurrent update"):
            self.commit((1, "head changed"), (0, MOVED), (0, formula_at_commit("newer formula\n")))

    def test_a_concurrent_formula_deletion_is_not_undone(self):
        with self.assertRaisesRegex(Failure, "refusing to overwrite its concurrent update"):
            self.commit((1, "head changed"), (0, MOVED), (0, formula_at_commit(None)))

    def test_a_new_formula_can_retry_after_another_formula_changes(self):
        calls = self.commit(
            (1, "head changed"), (0, MOVED), (0, formula_at_commit(None)), (0, COMMITTED), previous=None
        )
        self.assertEqual(self.change(calls[3])["expectedHeadOid"], MOVED)

    def test_a_failure_on_an_unmoved_tap_stops_with_the_reason(self):
        with self.assertRaisesRegex(Failure, "Resource not accessible by integration"):
            self.commit((1, '{"errors": [{"message": "Resource not accessible by integration"}]}'), (0, HEAD + "\n"))


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp)
        self.dist = self.temp / "dist"
        self.dist.mkdir()
        self.config = config(DBIRD)
        for target in self.config.release.targets:
            (self.dist / stacks.archive_name("dbird", target)).write_bytes(target.encode())
        self.tap = self.temp / "tap"

    def publish(self, env):
        with mock.patch.dict(os.environ, env), mock.patch.object(homebrew, "commit_to_tap") as commit:
            homebrew.publish(self.temp, self.config, "1.4.2", self.dist, self.tap)
        return commit

    def test_it_needs_the_apps_token(self):
        with self.assertRaisesRegex(Failure, "HOMEBREW_TAP_TOKEN"):
            self.publish({"GITHUB_REPOSITORY": "leduftw/dbird", "HOMEBREW_TAP_TOKEN": ""})

    def test_a_tap_that_has_the_formula_gets_no_commit(self):
        digests = homebrew._digests(self.config, self.dist)
        urls = {
            target: f"https://github.com/leduftw/dbird/releases/download/v1.4.2/{stacks.archive_name('dbird', target)}"
            for target in digests
        }
        (self.tap / "Formula").mkdir(parents=True)
        (self.tap / "Formula" / "dbird.rb").write_text(render(self.config, "1.4.2", "leduftw/dbird", urls, digests))
        commit = self.publish({"GITHUB_REPOSITORY": "leduftw/dbird", "HOMEBREW_TAP_TOKEN": "app-token"})
        commit.assert_not_called()

    def test_the_formula_is_installed_and_tested_before_the_api_commit(self):
        formula = self.tap / "Formula" / "dbird.rb"
        formula.parent.mkdir(parents=True)
        formula.write_text("old formula\n")
        events = []
        with (
            mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "leduftw/dbird", "HOMEBREW_TAP_TOKEN": "app-token"}),
            mock.patch.object(homebrew, "git", return_value=HEAD),
            mock.patch.object(homebrew, "run", side_effect=lambda *a, **kw: events.append("syntax")),
            mock.patch.object(homebrew, "_install_and_test", side_effect=lambda *a: events.append("install/test")),
            mock.patch.object(homebrew, "commit_to_tap", side_effect=lambda *a: events.append("commit")) as commit,
        ):
            homebrew.publish(self.temp, self.config, "1.4.2", self.dist, self.tap)
        self.assertEqual(events, ["syntax", "install/test", "commit"])
        self.assertEqual(commit.call_args.args[-1], "old formula\n")
        self.assertEqual(commit.call_args.args[1], formula.read_text())


if __name__ == "__main__":
    unittest.main()
