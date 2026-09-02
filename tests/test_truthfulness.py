"""The pipeline must not report changes it did not make.

Two eval cases taken from real failures reproduced against the committed data:

  1. A paraphrased `find` fuzzy-matches a line, the exact substring replace is a
     no-op, and a ChangeRecord is emitted anyway with from_text == to_text.
     Cookies, observed in docs/evidence/run-a.json.

  2. Every edit misses, so nothing changes, and the pipeline still publishes a
     file titled "(Community Enhanced)" with a citation and a stated impact.
     Spicy Apple Cake, observed in 4 of 8 full runs.

Run: .venv/bin/python tests/test_truthfulness.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# The pipeline constructor builds an OpenAI client. These tests never make a
# request; the extractor is replaced before any call. No key is needed or used.
os.environ.setdefault("OPENAI_API_KEY", "test-value-never-sent-anywhere")

from loguru import logger
logger.remove()

from llm_pipeline.models import ModificationEdit, ModificationObject, Recipe
from llm_pipeline.recipe_modifier import RecipeModifier
from llm_pipeline.pipeline import LLMAnalysisPipeline

COOKIES = REPO / "data" / "recipe_10813_best-chocolate-chip-cookies.json"
APPLE = REPO / "data" / "recipe_19117_spicy-apple-cake.json"


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Recipe(recipe_id=d["recipe_id"], title=d["title"],
                  ingredients=d["ingredients"], instructions=d["instructions"])


class TestNoChangeRecordWhenNothingChanged(unittest.TestCase):
    """Case 1: a replace that alters no text must not be reported as a change."""

    def setUp(self):
        self.recipe = load(COOKIES)
        self.modifier = RecipeModifier()

    def test_paraphrased_find_that_changes_nothing_reports_no_change(self):
        # "1 cup sugar" fuzzy-matches "1 cup white sugar" at 0.79, then the
        # exact substring replace finds nothing to replace.
        edit = ModificationEdit(target="ingredients", operation="replace",
                                find="1 cup sugar", replace="0.5 cup sugar")
        content, records = self.modifier.apply_edit(edit, self.recipe.ingredients)
        self.assertEqual(content, self.recipe.ingredients, "no text should have changed")
        self.assertEqual(records, [], "a replace that changed nothing must not be reported")

    def test_a_change_record_never_has_identical_from_and_to(self):
        edit = ModificationEdit(target="ingredients", operation="replace",
                                find="white sugar, 1 cup", replace="0.5 cup white sugar")
        _, records = self.modifier.apply_edit(edit, self.recipe.ingredients)
        for r in records:
            self.assertNotEqual(r.from_text, r.to_text,
                                "from_text == to_text is a change that did not happen")

    def test_a_genuine_replace_is_still_reported(self):
        edit = ModificationEdit(target="ingredients", operation="replace",
                                find="1 cup white sugar", replace="0.5 cup white sugar")
        content, records = self.modifier.apply_edit(edit, self.recipe.ingredients)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].from_text, "1 cup white sugar")
        self.assertEqual(records[0].to_text, "0.5 cup white sugar")
        self.assertIn("0.5 cup white sugar", content)

    def test_total_changes_counts_only_real_changes(self):
        mod = ModificationObject(
            modification_type="quantity_adjustment",
            reasoning="Halve the sugar.",
            edits=[
                ModificationEdit(target="ingredients", operation="replace",
                                 find="1 cup white sugar", replace="0.5 cup white sugar"),
                ModificationEdit(target="ingredients", operation="replace",
                                 find="1 cup sugar", replace="0.5 cup sugar"),
            ],
        )
        _, records = self.modifier.apply_modification(self.recipe, mod)
        self.assertEqual(len(records), 1, "only the edit that altered text should be counted")


class TestNoPublishWhenNothingApplied(unittest.TestCase):
    """Case 2: zero applied edits must fail the run, not publish a recipe."""

    def _pipeline_with(self, modification, outdir):
        """Every tweak yields the same modification, so the whole run turns on it."""
        p = LLMAnalysisPipeline(output_dir=outdir)
        p.tweak_extractor.extract_modifications = lambda review, recipe, **kw: [modification]
        return p

    def test_a_modification_whose_edits_all_miss_does_not_publish(self):
        # "2 cups apple" scores 0.53 against the real line and is dropped.
        mod = ModificationObject(
            modification_type="quantity_adjustment",
            reasoning="More apple chunks.",
            edits=[ModificationEdit(target="ingredients", operation="replace",
                                    find="2 cups apple", replace="3 cups apple")],
        )
        with tempfile.TemporaryDirectory() as d:
            result = self._pipeline_with(mod, d).process_single_recipe(str(APPLE), save_output=True)
            self.assertIsNone(result, "a run that changed nothing must not return a recipe")
            written = list(Path(d).glob("enhanced_*.json"))
            self.assertEqual(written, [], f"nothing should have been published, found {written}")

    def test_a_modification_that_applies_still_publishes(self):
        mod = ModificationObject(
            modification_type="quantity_adjustment",
            reasoning="More apple chunks.",
            edits=[ModificationEdit(
                target="ingredients", operation="replace",
                find="2 cups apple - peeled, cored, and chopped",
                replace="3 cups apple - peeled, cored, and chopped")],
        )
        with tempfile.TemporaryDirectory() as d:
            result = self._pipeline_with(mod, d).process_single_recipe(str(APPLE), save_output=True)
            self.assertIsNotNone(result, "a genuine change must still publish")
            self.assertEqual(result.enhancement_summary.total_changes, 1)
            self.assertEqual(len(list(Path(d).glob("enhanced_*.json"))), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
