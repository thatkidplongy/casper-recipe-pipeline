"""Selection must be deterministic, complete, and attributable.

Today `extract_single_modification` calls `random.choice` over every review
flagged has_modification. Consequences reproduced against the real data:

  * three consecutive runs published three different recipes from one input
  * roughly one run in four publishes the 3-star reviewer's version
  * the scraped `featured_tweaks` list, the only ranking signal captured, is
    never read
  * nothing in the output says which tweak produced which change, so a run
    cannot be reproduced or explained

Run: .venv/bin/python tests/test_selection.py
"""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("OPENAI_API_KEY", "test-value-never-sent-anywhere")

from loguru import logger
logger.remove()

from llm_pipeline.models import ModificationEdit, ModificationObject
from llm_pipeline.pipeline import LLMAnalysisPipeline

COOKIES = REPO / "data" / "recipe_10813_best-chocolate-chip-cookies.json"

# One extraction per featured tweak, in the order the tweaks appear.
BY_CUE = {
    "ice cream scoop": ("addition", "An extra egg yolk keeps the cookie chewy.",
        [("add_after", "2 eggs", None, "1 additional egg yolk")]),
    "advice of others": ("quantity_adjustment", "More brown sugar than white.",
        [("replace", "1 cup white sugar", "0.5 cup white sugar", None),
         ("remove", "2 teaspoons hot water", None, None)]),
    "bit bland": ("quantity_adjustment", "More salt lifts a bland cookie.",
        [("replace", "0.5 teaspoon salt", "1 teaspoon salt", None)]),
    "whole cup of white sugar": ("addition", "A dash of cinnamon.",
        [("add_after", "2 teaspoons vanilla extract", None, "1 dash ground cinnamon")]),
}


def canned(review_text):
    for cue, (mtype, reason, edits) in BY_CUE.items():
        if cue in review_text:
            return ModificationObject(
                modification_type=mtype, reasoning=reason,
                edits=[ModificationEdit(target="ingredients", operation=op,
                                        find=f, replace=r, add=a)
                       for op, f, r, a in edits])
    return None


def build(outdir):
    p = LLMAnalysisPipeline(output_dir=outdir)
    mods = lambda review, recipe, **kw: [m for m in [canned(review.text)] if m]
    p.tweak_extractor.extract_modifications = mods
    return p


def run_once(outdir):
    return build(outdir).process_single_recipe(str(COOKIES), save_output=True)


class TestDeterminism(unittest.TestCase):
    def test_two_runs_produce_the_same_recipe(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = run_once(a), run_once(b)
            self.assertEqual(ra.ingredients, rb.ingredients, "same input must give same recipe")
            self.assertEqual(ra.instructions, rb.instructions)

    def test_two_runs_cite_the_same_sources_in_the_same_order(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = run_once(a), run_once(b)
            self.assertEqual([m.source_tweak_id for m in ra.modifications_applied],
                             [m.source_tweak_id for m in rb.modifications_applied])

    def test_no_random_selection_remains_in_the_pipeline(self):
        for name in ("tweak_extractor.py", "pipeline.py"):
            src = (REPO / "src" / "llm_pipeline" / name).read_text()
            self.assertNotIn("random.choice", src, f"{name} still selects at random")
            self.assertFalse(re.search(r"^\s*import random", src, re.M),
                             f"{name} still imports random")


class TestSummaryIsStable(unittest.TestCase):
    """`list(set(...))` reorders between processes because string hashing is
    randomised, so two identical runs produced different files."""

    def test_change_types_are_stable_across_processes(self):
        from llm_pipeline.enhanced_recipe_generator import EnhancedRecipeGenerator
        from llm_pipeline.models import ModificationApplied, SourceReview

        def applied(mtype, rank):
            return ModificationApplied(
                source_review=SourceReview(text="t", reviewer=None, rating=5),
                modification_type=mtype, reasoning="r", changes_made=[],
                source_tweak_id=f"x-t{rank}", source_tweak_rank=rank)

        mods = [applied("quantity_adjustment", 1), applied("addition", 2),
                applied("removal", 3), applied("technique_change", 4),
                applied("addition", 5)]
        got = EnhancedRecipeGenerator().calculate_enhancement_summary(mods).change_types
        self.assertEqual(got, ["quantity_adjustment", "addition", "removal", "technique_change"],
                         "change_types must be deduplicated in rank order, not set order")

    def test_the_whole_saved_file_is_byte_identical_across_runs(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            run_once(a); run_once(b)
            fa = json.loads(next(Path(a).glob("enhanced_*.json")).read_text())
            fb = json.loads(next(Path(b).glob("enhanced_*.json")).read_text())
            fa.pop("created_at"); fb.pop("created_at")
            self.assertEqual(fa, fb, "two runs on one input must produce the same file")


class TestCompleteness(unittest.TestCase):
    def test_every_featured_tweak_is_processed_not_one(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_once(d)
            self.assertEqual(len(r.modifications_applied), 4,
                             "the cookie recipe has 4 featured tweaks; all must be applied")

    def test_changes_from_every_tweak_reach_the_recipe(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_once(d)
            self.assertIn("1 additional egg yolk", r.ingredients)
            self.assertIn("0.5 cup white sugar", r.ingredients)
            self.assertIn("1 teaspoon salt", r.ingredients)
            self.assertIn("1 dash ground cinnamon", r.ingredients)
            self.assertNotIn("2 teaspoons hot water", r.ingredients)


class TestAttribution(unittest.TestCase):
    def test_each_modification_names_its_source_tweak(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_once(d)
            for m in r.modifications_applied:
                self.assertTrue(m.source_tweak_id, "every modification must name its tweak")
                self.assertIsInstance(m.source_tweak_rank, int)

    def test_tweaks_are_applied_in_ranked_list_order(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_once(d)
            ranks = [m.source_tweak_rank for m in r.modifications_applied]
            self.assertEqual(ranks, sorted(ranks), "tweaks must be applied in rank order")
            self.assertEqual(ranks, [1, 2, 3, 4])

    def test_the_saved_file_carries_the_attribution(self):
        with tempfile.TemporaryDirectory() as d:
            run_once(d)
            saved = json.loads(next(Path(d).glob("enhanced_*.json")).read_text())
            ids = [m["source_tweak_id"] for m in saved["modifications_applied"]]
            self.assertEqual(ids, ["10813-t1", "10813-t2", "10813-t3", "10813-t4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
