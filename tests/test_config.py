import tomllib
import unittest

from playbook.config import TARGETS, ConfigError, parse
from tests.fixtures import DBIRD, LOCAL, MONAD, config


class ValidConfigTest(unittest.TestCase):
    def test_dbird_shape_fills_in_defaults(self):
        dbird = config(DBIRD)
        self.assertTrue(dbird.published)
        self.assertEqual(dbird.release.binaries, ["dbird"])
        self.assertEqual(dbird.release.targets, list(TARGETS))
        self.assertEqual(dbird.release.registry, "crates-io")
        self.assertEqual(dbird.release.winget.id, "leduftw.dbird")
        self.assertEqual(dbird.checks[1].skip, {"format": "no formatter configured", "lint": "no linter configured"})

    def test_monad_shape_keeps_its_specifics(self):
        monad = config(MONAD)
        self.assertEqual(monad.release.registry, "nuget")
        self.assertEqual(monad.release.macos_binaries, ["monad-audiotap"])
        self.assertEqual(monad.release.version_output, "monad {version}")
        self.assertEqual(monad.release.smoke["macos"], ["{dir}/monad-audiotap help"])

    def test_local_repo_checks_only_on_linux(self):
        local = config(LOCAL)
        self.assertFalse(local.published)
        self.assertIsNone(local.release)
        self.assertEqual(local.check_os(local.checks[0]), ["linux"])

    def test_published_repo_checks_everywhere(self):
        dbird = config(DBIRD)
        self.assertEqual(dbird.check_os(dbird.checks[0]), ["linux", "macos", "windows"])


class InvalidConfigTest(unittest.TestCase):
    def assertInvalid(self, text, message):
        with self.assertRaises(ConfigError) as caught:
            parse(tomllib.loads(text))
        self.assertIn(message, str(caught.exception))

    def test_unknown_keys_are_errors(self):
        self.assertInvalid('name = "x"\npublihsed = true\n[[check]]\nstack = "rust"', "unknown key(s) publihsed")

    def test_a_check_is_required(self):
        self.assertInvalid('name = "x"', "at least one [[check]]")

    def test_published_needs_a_release_table(self):
        self.assertInvalid('name = "x"\npublished = true\n[[check]]\nstack = "rust"', "needs a [release] table")

    def test_release_table_needs_published(self):
        self.assertInvalid(
            'name = "x"\n[[check]]\nstack = "rust"\n[release]\nstack = "rust"', "only applies to published"
        )

    def test_registry_must_match_the_stack(self):
        text = (
            'name = "x"\npublished = true\n[[check]]\nstack = "rust"\n[release]\nstack = "rust"\nchannels = ["nuget"]'
        )
        self.assertInvalid(text, "nuget can't publish a rust project")

    def test_homebrew_needs_a_description(self):
        text = (
            'name = "x"\npublished = true\n[[check]]\nstack = "rust"\n'
            '[release]\nstack = "rust"\nchannels = ["homebrew"]'
        )
        self.assertInvalid(text, "release.homebrew.desc is required")

    def test_dotnet_release_needs_a_project(self):
        text = 'name = "x"\npublished = true\n[[check]]\nstack = "dotnet"\n[release]\nstack = "dotnet"'
        self.assertInvalid(text, "release.project is required")

    def test_skipping_needs_a_reason_and_no_command(self):
        self.assertInvalid('name = "x"\n[[check]]\nstack = "rust"\nskip.lint = ""', "needs the reason")
        self.assertInvalid(
            'name = "x"\n[[check]]\nstack = "rust"\nlint = ["x"]\nskip.lint = "no"', "both configured and skipped"
        )

    def test_unknown_macos_version_is_rejected(self):
        text = (
            'name = "x"\npublished = true\n[[check]]\nstack = "rust"\n[release]\nstack = "rust"\nmacos-minimum = "16.1"'
        )
        self.assertInvalid(text, "macos-minimum")

    def test_names_are_lowercase_with_hyphens(self):
        self.assertInvalid('name = "My App"\n[[check]]\nstack = "rust"', "lowercase letters")


if __name__ == "__main__":
    unittest.main()
