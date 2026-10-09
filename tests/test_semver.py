import unittest

from playbook import semver
from playbook.semver import Version


class ParseTest(unittest.TestCase):
    def test_plain_and_tagged_versions_parse(self):
        self.assertEqual(semver.parse("1.2.3"), Version(1, 2, 3))
        self.assertEqual(semver.parse("v10.0.0"), Version(10, 0, 0))

    def test_anything_else_is_rejected(self):
        for text in ("1.2", "1.2.3.4", "01.2.3", "1.2.3-rc1", "v", "latest", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                semver.parse(text)


class NextVersionTest(unittest.TestCase):
    def test_first_release_is_always_one_point_zero(self):
        for kind in semver.BUMPS:
            with self.subTest(kind=kind):
                self.assertEqual(str(semver.next_version(None, kind)), "1.0.0")

    def test_bumps_reset_the_lower_parts(self):
        latest = Version(1, 2, 3)
        self.assertEqual(str(semver.next_version(latest, "patch")), "1.2.4")
        self.assertEqual(str(semver.next_version(latest, "minor")), "1.3.0")
        self.assertEqual(str(semver.next_version(latest, "major")), "2.0.0")

    def test_unknown_bump_is_rejected(self):
        with self.assertRaises(ValueError):
            semver.next_version(Version(1, 0, 0), "huge")


class CheckNextTest(unittest.TestCase):
    def test_first_release_must_be_one_point_zero(self):
        self.assertIsNone(semver.check_next(None, Version(1, 0, 0)))
        problem = semver.check_next(None, Version(2, 0, 0))
        self.assertIn("first release must be 1.0.0", problem)

    def test_only_the_three_next_steps_are_allowed(self):
        latest = Version(1, 2, 0)
        for allowed in ("1.2.1", "1.3.0", "2.0.0"):
            with self.subTest(allowed=allowed):
                self.assertIsNone(semver.check_next(latest, semver.parse(allowed)))
        for gap in ("1.4.0", "3.0.0", "1.2.2", "1.2.0", "1.1.9", "2.0.1"):
            with self.subTest(gap=gap):
                self.assertIn("can't follow 1.2.0", semver.check_next(latest, semver.parse(gap)))


class LatestTagTest(unittest.TestCase):
    def test_highest_full_version_wins_and_moving_tags_are_ignored(self):
        tags = ["v1", "v1.0.0", "v1.10.0", "v1.9.3", "v2.0.0-rc1", "nightly", "v2"]
        self.assertEqual(semver.latest_tag(tags), Version(1, 10, 0))

    def test_no_release_tags_means_none(self):
        self.assertIsNone(semver.latest_tag(["v1", "nightly"]))


if __name__ == "__main__":
    unittest.main()
