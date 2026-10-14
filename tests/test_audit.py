import datetime
import unittest

from playbook.audit import waiting_dependabot

NOW = datetime.datetime(2026, 10, 14, 12, tzinfo=datetime.UTC)


class WaitingDependabotTest(unittest.TestCase):
    def test_reports_only_prs_older_than_a_week(self):
        prs = [
            {"number": 307, "title": "bump vite from 7 to 8", "createdAt": "2026-10-01T09:00:00Z"},
            {"number": 308, "title": "bump the minor-and-patch group", "createdAt": "2026-10-13T22:55:32Z"},
            {"number": 265, "title": "bump actions/checkout from 6 to 7", "createdAt": "2026-08-17T10:00:00Z"},
        ]
        self.assertEqual(
            waiting_dependabot(prs, NOW),
            [
                "Dependabot PR #265 has waited 58 days: bump actions/checkout from 6 to 7",
                "Dependabot PR #307 has waited 13 days: bump vite from 7 to 8",
            ],
        )

    def test_nothing_waiting(self):
        self.assertEqual(waiting_dependabot([], NOW), [])


if __name__ == "__main__":
    unittest.main()
