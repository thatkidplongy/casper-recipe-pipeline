"""Tests for the golden set scorer.

Stdlib unittest, no third-party dependency, same reasoning as the exporter.
Run: python3 scripts/test_run_golden_set.py
"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_golden_set import (matches_anchor, score_fixture, aggregate,
                            per_fixture_line, run_fixtures, AllCallsFailed,
                            live_extractor, render_run_log, display_path,
                            configure_logging)


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


class TestAFailedRunMustNotScoreItself(unittest.TestCase):
    """The harness had the defect it was built to measure.

    Every call raised, every fixture scored as "the model returned nothing", and
    the run reported recall 0% with zero-tweaks correct 2/2. It credited itself
    for the two fixtures that expect nothing, because a total failure looks
    exactly like a correct empty answer.
    """

    def _fixtures(self):
        return [
            fixture("a", mods=[mod("m1", "1 cup white sugar")]),
            fixture("z", excluded=[{"quote": "next time", "reason": "future_intent"}]),
        ]

    def test_a_run_where_every_call_failed_aborts(self):
        def boom(fx, recipe):
            raise RuntimeError("404 model not found")
        with self.assertRaises(AllCallsFailed) as caught:
            run_fixtures(self._fixtures(), {"r": {}}, boom)
        self.assertIn("404", str(caught.exception), "the abort must carry the real cause")

    def test_a_zero_tweak_is_not_credited_when_the_call_failed(self):
        """Returning nothing because the API errored is not a correct answer."""
        r = score_fixture(fixture("z", excluded=[{"quote": "x", "reason": "future_intent"}]),
                          [], failed=True)
        self.assertTrue(r["failed"])
        self.assertFalse(r["zero_tweak_correct"],
                         "a failed call must not score as correctly returning nothing")

    def test_a_zero_tweak_is_still_credited_on_a_real_empty_answer(self):
        r = score_fixture(fixture("z", excluded=[{"quote": "x", "reason": "future_intent"}]), [])
        self.assertFalse(r["failed"])
        self.assertTrue(r["zero_tweak_correct"])

    def test_partial_failure_is_counted_and_reported(self):
        calls = {"n": 0}
        def flaky(fx, recipe):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return []
        results, errors = run_fixtures(self._fixtures(), {"r": {}}, flaky)
        self.assertEqual(len(errors), 1)
        self.assertEqual(sum(1 for r in results if r["failed"]), 1)

    def test_aggregate_surfaces_failures(self):
        results, _ = run_fixtures(self._fixtures(), {"r": {}}, lambda fx, rc: [])
        agg = aggregate([results])
        self.assertIn("failed", agg)
        self.assertEqual(agg["failed"], 0)


class TestProgressIsVisible(unittest.TestCase):
    """A run of 120 extractions with no output until the end is
    indistinguishable from a hang. That is the same missing distinction."""

    def test_each_fixture_reports_as_it_completes(self):
        seen = []
        fixtures = [fixture("a", mods=[mod("m1", "1 cup white sugar")]),
                    fixture("b", mods=[mod("m1", "2 eggs")])]
        run_fixtures(fixtures, {"r": {}}, lambda fx, rc: [],
                     on_result=lambda r: seen.append(r["tweak_id"]))
        self.assertEqual(seen, ["a", "b"], "progress must arrive per fixture, in order")

    def test_a_failure_is_reported_as_it_happens(self):
        seen = []
        fixtures = [fixture("a"), fixture("b", mods=[mod("m1", "2 eggs")])]
        def half(fx, rc):
            if fx["tweak_id"] == "a":
                raise RuntimeError("boom")
            return []
        run_fixtures(fixtures, {"r": {}}, half, on_result=lambda r: seen.append(r["failed"]))
        self.assertEqual(seen, [True, False])


class TestTheHarnessCallsAMethodThatExists(unittest.TestCase):
    """live_extractor called extract_modification after it was renamed to
    extract_modifications. Attribute lookup on a live object fails only at call
    time, and the harness swallowed the AttributeError as an empty answer."""

    def test_every_extractor_method_the_harness_calls_is_defined(self):
        """Read both files as text. No imports, so this runs with no
        dependencies installed and cannot itself fail open."""
        import re
        from pathlib import Path

        here = Path(__file__).resolve().parent
        harness = (here / "run_golden_set.py").read_text()
        extractor = (here.parent / "src" / "llm_pipeline" / "tweak_extractor.py").read_text()

        defined = set(re.findall(r"^\s*def (\w+)\(", extractor, re.M))
        called = set(re.findall(r"\bex\.(\w+)\(", harness))
        self.assertTrue(called, "expected the harness to call the extractor")
        missing = called - defined
        self.assertEqual(missing, set(),
                         f"harness calls TweakExtractor.{missing}, not defined in "
                         f"tweak_extractor.py; attribute lookup fails only at call time")


class TestTheRunLogIsDemonstrableEvidence(unittest.TestCase):
    """A terminal scrollback is not evidence. The log must stand alone: what was
    run, against which model and endpoint, at which commit, and what came back."""

    def _log(self, **over):
        meta = {"mode": "LIVE m @ https://api.groq.com/openai/v1/", "model": "m",
                "endpoint": "https://api.groq.com/openai/v1/", "commit": "abc1234",
                "started_at": "2026-09-02T09:55:00Z", "runs": 2, "fixtures": 2,
                "dirty": False}
        meta.update(over)
        results = [
            [score_fixture(fixture("a", mods=[mod("m1", "1 cup white sugar")]),
                           [edit("1 cup white sugar")]),
             score_fixture(fixture("z", excluded=[{"quote": "q", "reason": "future_intent"}]), [])],
        ]
        raw = {"a": '{"modifications": [{"modification_type": "quantity_adjustment"}]}'}
        return render_run_log(meta, results, [], raw)

    def test_it_records_what_was_run_against_what(self):
        log = self._log()
        for needed in ("abc1234", "https://api.groq.com/openai/v1/", "2026-09-02T09:55:00Z"):
            self.assertIn(needed, log, f"provenance missing: {needed}")

    def test_it_records_each_fixture_result(self):
        log = self._log()
        self.assertIn("a", log)
        self.assertIn("z", log)

    def test_it_includes_the_raw_model_output(self):
        self.assertIn("modification_type", self._log(),
                      "the raw response is the evidence; a summary is a claim about it")

    def test_it_warns_when_the_tree_was_dirty(self):
        self.assertIn("uncommitted", self._log(dirty=True).lower(),
                      "a result from an unrecorded tree is not reproducible")

    def test_credentials_never_reach_the_log(self):
        secret = "gsk_" + "z" * 44
        log = self._log(endpoint=f"https://api.groq.com/openai/v1/?key={secret}")
        self.assertNotIn(secret, log)

    def test_a_failed_fixture_is_visible_as_failed(self):
        meta = {"mode": "LIVE", "model": "m", "endpoint": "e", "commit": "c",
                "started_at": "t", "runs": 1, "fixtures": 1, "dirty": False}
        results = [[score_fixture(fixture("a", mods=[mod("m1", "x")]), [], failed=True)]]
        log = render_run_log(meta, results, [("a", "RuntimeError: boom")], {})
        self.assertIn("boom", log)


class TestPathsOutsideTheRepoDoNotCrash(unittest.TestCase):
    """--log and --out accept any path. relative_to raises for anything outside
    the repository, so the run would finish its work and then die printing where
    it put the results."""

    def test_a_path_inside_the_repo_is_shown_relative(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        self.assertEqual(display_path(repo / "docs" / "x.md"), "docs/x.md")

    def test_a_path_outside_the_repo_is_shown_absolutely(self):
        from pathlib import Path
        out = display_path(Path("/tmp/elsewhere/x.md"))
        self.assertIn("elsewhere", out, "an outside path must still be printable")


class TestCompletedRunsSurviveAnAbort(unittest.TestCase):
    """Run 1 completed, run 2 hit a daily token cap, and the abort returned
    before writing anything. A completed measurement was destroyed by a later
    failure. Partial evidence is evidence."""

    def test_the_log_renders_from_the_runs_that_did_complete(self):
        meta = {"mode": "LIVE m @ e", "model": "m", "endpoint": "e", "commit": "c",
                "started_at": "t", "runs": 2, "fixtures": 1, "dirty": False,
                "completed_runs": 1, "aborted": "run 2: rate limit reached"}
        results = [[score_fixture(fixture("a", mods=[mod("m1", "1 cup white sugar")]),
                                  [edit("1 cup white sugar")])]]
        log = render_run_log(meta, results, [], {})
        self.assertIn("| 1 | `a` | 1/1 |", log,
                      "the completed run's result must survive the later abort")
        self.assertIn("rate limit reached", log, "the abort cause must be recorded")

    def test_the_log_says_it_is_incomplete(self):
        meta = {"mode": "LIVE", "model": "m", "endpoint": "e", "commit": "c",
                "started_at": "t", "runs": 10, "fixtures": 1, "dirty": False,
                "completed_runs": 1, "aborted": "cap"}
        log = render_run_log(meta,
                             [[score_fixture(fixture("a", mods=[mod("m1", "x")]), [])]],
                             [], {})
        low = log.lower()
        self.assertIn("incomplete", low,
                      "a run of 1 reported as 10 would overstate the measurement")
        self.assertIn("1 of 10", low)

    def test_a_complete_run_is_not_labelled_incomplete(self):
        meta = {"mode": "LIVE", "model": "m", "endpoint": "e", "commit": "c",
                "started_at": "t", "runs": 1, "fixtures": 1, "dirty": False,
                "completed_runs": 1, "aborted": None}
        log = render_run_log(meta,
                             [[score_fixture(fixture("a", mods=[mod("m1", "x")]), [])]],
                             [], {})
        self.assertNotIn("incomplete", log.lower())


class TestRawResponsesAreLabelledWithTheirRun(unittest.TestCase):
    """A failed call records no response, so the entry for a fixture may come
    from run 2 or 3. Claiming "from the first run" would be false."""

    def test_the_run_number_is_stated(self):
        meta = {"mode": "LIVE", "model": "m", "endpoint": "e", "commit": "c",
                "started_at": "t", "runs": 3, "fixtures": 1, "dirty": False,
                "completed_runs": 3, "aborted": None}
        results = [[score_fixture(fixture("a", mods=[mod("m1", "x")]), [])]]
        log = render_run_log(meta, results, [], {"a": (2, '{"modifications": []}')})
        self.assertIn("run 2", log,
                      "the log must say which run each response came from")

    def test_a_plain_string_still_renders(self):
        """Backward compatibility with the older sink shape."""
        meta = {"mode": "LIVE", "model": "m", "endpoint": "e", "commit": "c",
                "started_at": "t", "runs": 1, "fixtures": 1, "dirty": False,
                "completed_runs": 1, "aborted": None}
        results = [[score_fixture(fixture("a", mods=[mod("m1", "x")]), [])]]
        log = render_run_log(meta, results, [], {"a": '{"modifications": []}'})
        self.assertIn("modifications", log)


class TestTerminalOutputIsWatchable(unittest.TestCase):
    """Raw model responses are captured in the log file. Dumping them to stdout
    as well buries the progress lines a viewer is trying to follow."""

    def test_the_default_level_hides_raw_response_dumps(self):
        self.assertEqual(configure_logging(verbose=False), "WARNING")

    def test_verbose_restores_the_detail(self):
        self.assertEqual(configure_logging(verbose=True), "DEBUG")


class TestTheLogExplainsItself(unittest.TestCase):
    """A demo audience cannot read a count. The log must name what was expected
    and not found, and what was produced that nothing expected."""

    def _meta(self, **over):
        m = {"mode": "LIVE m @ e", "model": "m", "endpoint": "e", "commit": "c",
             "started_at": "t", "runs": 1, "fixtures": 1, "dirty": False,
             "completed_runs": 1, "aborted": None}
        m.update(over)
        return m

    def test_a_missed_modification_is_named(self):
        fx = fixture("a", mods=[mod("m1", "0.5 teaspoon salt"),
                                mod("m2", "1 cup chopped walnuts")])
        fx["expected_modifications"][1]["intent"] = "Omit the walnuts."
        r = score_fixture(fx, [edit("0.5 teaspoon salt")])
        log = render_run_log(self._meta(), [[r]], [], {})
        self.assertIn("Omit the walnuts.", log,
                      "the log must say which modification was not found")

    def test_a_spurious_edit_is_shown(self):
        fx = fixture("a", mods=[mod("m1", "0.5 teaspoon salt")])
        r = score_fixture(fx, [edit("0.5 teaspoon salt"), edit("2 cups semisweet chocolate chips")])
        log = render_run_log(self._meta(), [[r]], [], {})
        self.assertIn("2 cups semisweet chocolate chips", log,
                      "the log must show what the model produced that nothing expected")

    def test_a_clean_fixture_needs_no_explanation(self):
        fx = fixture("a", mods=[mod("m1", "0.5 teaspoon salt")])
        r = score_fixture(fx, [edit("0.5 teaspoon salt")])
        log = render_run_log(self._meta(), [[r]], [], {})
        self.assertNotIn("Not found", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
