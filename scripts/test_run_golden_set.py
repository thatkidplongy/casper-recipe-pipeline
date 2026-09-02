"""Tests for the golden set scorer.

Stdlib unittest, no third-party dependency, same reasoning as the exporter.
Run: python3 scripts/test_run_golden_set.py
"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_golden_set import matches_anchor, score_fixture, aggregate, per_fixture_line


def fixture(tweak_id="t", mods=(), excluded=()):
    return {"tweak_id": tweak_id, "recipe_id": "r", "review_text": "x",
            "expected_modifications": list(mods), "excluded": list(excluded)}


def mod(i, anchor, spec="exact", target="ingredients", mtype="quantity_adjustment"):
    return {"id": i, "type": mtype, "target": target, "intent": "x",
            "anchor": anchor, "specificity": spec}


def edit(find, target="ingredients", op="replace"):
    return {"target": target, "operation": op, "find": find, "replace": "y", "add": None}


class TestAnchorMatching(unittest.TestCase):
    def test_identical_text_matches(self):
        self.assertTrue(matches_anchor(edit("1 cup white sugar"), "1 cup white sugar", "ingredients"))

    def test_substring_of_the_anchor_matches(self):
        self.assertTrue(matches_anchor(edit("2 cups apple"), "2 cups apple - peeled, cored, and chopped", "ingredients"))

    def test_different_ingredient_does_not_match(self):
        self.assertFalse(matches_anchor(edit("1 cup chopped walnuts"), "0.5 teaspoon salt", "ingredients"))

    def test_wrong_target_does_not_match(self):
        self.assertFalse(matches_anchor(edit("1 cup white sugar", target="instructions"), "1 cup white sugar", "ingredients"))

    def test_anchorless_expectation_never_matches_by_anchor(self):
        self.assertFalse(matches_anchor(edit("anything"), None, "ingredients"))


class TestScoringRecall(unittest.TestCase):
    def test_exact_modification_found_is_recalled(self):
        f = fixture(mods=[mod("m1", "1 cup white sugar")])
        r = score_fixture(f, [edit("1 cup white sugar")])
        self.assertEqual(r["exact_expected"], 1)
        self.assertEqual(r["exact_found"], 1)
        self.assertEqual(r["spurious"], 0)

    def test_exact_modification_missed_counts_against_recall(self):
        f = fixture(mods=[mod("m1", "1 cup white sugar")])
        r = score_fixture(f, [])
        self.assertEqual(r["exact_expected"], 1)
        self.assertEqual(r["exact_found"], 0)

    def test_underspecified_missed_is_not_a_miss(self):
        """Declining to invent a quantity is correct behaviour."""
        f = fixture(mods=[mod("m1", "0.25 cup soy sauce", spec="underspecified")])
        r = score_fixture(f, [])
        self.assertEqual(r["exact_expected"], 0, "underspecified must not enter the recall denominator")
        self.assertEqual(r["underspec_expected"], 1)
        self.assertEqual(r["underspec_found"], 0)

    def test_underspecified_found_is_credited_but_not_required(self):
        f = fixture(mods=[mod("m1", "0.25 cup soy sauce", spec="underspecified")])
        r = score_fixture(f, [edit("0.25 cup soy sauce")])
        self.assertEqual(r["underspec_found"], 1)
        self.assertEqual(r["spurious"], 0, "matching an underspecified expectation is not spurious")


class TestScoringFabrication(unittest.TestCase):
    def test_edit_matching_nothing_expected_is_spurious(self):
        f = fixture(mods=[mod("m1", "1 cup white sugar")])
        r = score_fixture(f, [edit("1 cup white sugar"), edit("1 cup chopped walnuts")])
        self.assertEqual(r["spurious"], 1)

    def test_any_edit_on_a_zero_modification_tweak_is_fabrication(self):
        """The reviewer described no change. Any edit is invented."""
        f = fixture(mods=[], excluded=[{"quote": "I will use fresh ginger", "reason": "future_intent"}])
        r = score_fixture(f, [edit("1.5 teaspoons ground ginger")])
        self.assertTrue(r["is_zero_tweak"])
        self.assertFalse(r["zero_tweak_correct"])
        self.assertEqual(r["spurious"], 1)

    def test_returning_nothing_on_a_zero_modification_tweak_passes(self):
        f = fixture(mods=[], excluded=[{"quote": "x", "reason": "future_intent"}])
        r = score_fixture(f, [])
        self.assertTrue(r["is_zero_tweak"])
        self.assertTrue(r["zero_tweak_correct"])


class TestAggregate(unittest.TestCase):
    def test_pass_rate_is_over_exact_modifications(self):
        runs = [
            [{"tweak_id": "a", "exact_expected": 2, "exact_found": 1, "underspec_expected": 0,
              "underspec_found": 0, "spurious": 0, "is_zero_tweak": False, "zero_tweak_correct": None}],
            [{"tweak_id": "a", "exact_expected": 2, "exact_found": 2, "underspec_expected": 0,
              "underspec_found": 0, "spurious": 1, "is_zero_tweak": False, "zero_tweak_correct": None}],
        ]
        agg = aggregate(runs)
        self.assertEqual(agg["exact_expected"], 4)
        self.assertEqual(agg["exact_found"], 3)
        self.assertAlmostEqual(agg["recall"], 0.75)
        self.assertEqual(agg["spurious"], 1)
        self.assertEqual(agg["runs"], 2)


class TestPerFixtureReporting(unittest.TestCase):
    def test_fixture_with_only_underspecified_expectations_does_not_crash(self):
        """19117-t1 has one expectation and it is underspecified, so the
        recall denominator is empty. That is not a zero-tweak."""
        line = per_fixture_line("19117-t1", {"e": 0, "f": 0, "sp": 0, "z": False, "zc": 0, "zn": 0})
        self.assertIn("19117-t1", line)
        self.assertIn("no exact", line)

    def test_ordinary_fixture_reports_a_percentage(self):
        line = per_fixture_line("a", {"e": 4, "f": 3, "sp": 1, "z": False, "zc": 0, "zn": 0})
        self.assertIn("75%", line)

    def test_zero_tweak_reports_invented_edits(self):
        line = per_fixture_line("z", {"e": 0, "f": 0, "sp": 2, "z": True, "zc": 1, "zn": 3})
        self.assertIn("1/3", line)
        self.assertIn("2 invented", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
