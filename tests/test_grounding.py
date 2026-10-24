import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from grounding import ungrounded_numbers  # noqa: E402

ROWS = [["Soho", 1828971.0, 62325], ["Headingley", 1150854.4, 39323], ["Share", 0.3104, 0]]


class Grounding(unittest.TestCase):
    def test_exact_and_rounded(self):
        self.assertEqual(ungrounded_numbers("Soho made £1,828,971 from 62,325 orders.", ROWS), [])
        self.assertEqual(ungrounded_numbers("Headingley made £1,150,854.", ROWS), [])
        self.assertEqual(ungrounded_numbers("Soho made about £1.8 million.", ROWS), [])

    def test_percentages(self):
        self.assertEqual(ungrounded_numbers("Delivery was 31% of orders.", ROWS), [])

    def test_sign_is_ignored(self):
        rows = [["London", -0.1, -0.5]]
        self.assertEqual(ungrounded_numbers("London fell 0.1 points and Bristol fell 0.5 points.", rows), [])

    def test_catches_invented_numbers(self):
        self.assertEqual(ungrounded_numbers("Soho made £2,500,000.", ROWS), ["£2,500,000"])
        self.assertEqual(ungrounded_numbers("Soho made £2.5 million.", ROWS), ["£2.5 million"])
        self.assertEqual(ungrounded_numbers("Revenue fell 47%.", ROWS), ["47%"])

    def test_ignores_dates_years_small_counts(self):
        self.assertEqual(ungrounded_numbers("Between 2026-10-01 and 14 October 2026 there were 3 sites.", ROWS), [])

    def test_question_numbers_allowed(self):
        self.assertEqual(ungrounded_numbers("Over the last 90 days, Soho led.", ROWS, "last 90 days"), [])


if __name__ == "__main__":
    unittest.main()
