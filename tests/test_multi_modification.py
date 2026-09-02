"""One review can describe several discrete modifications.

Fixture 10813-t2 is the worst case in the corpus. The reviewer lists four
numbered tweaks that decompose into five discrete modifications spanning four
categories:

    (1) half cup of sugar and one-and-a-half cups of brown sugar
        -> quantity_adjustment x2
    (2) omitted the water                  -> removal
    (3) added a teaspoon of cream of tartar -> addition
    (4) refrigerated the batter            -> technique_change

ModificationObject carries one modification_type and one reasoning, so
everything after the first category is lost. Six of the twelve fixtures span
more than one category, so half the corpus is structurally unrepresentable.

Run: .venv/bin/python tests/test_multi_modification.py
"""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("OPENAI_API_KEY", "test-value-never-sent-anywhere")

from loguru import logger
logger.remove()

from llm_pipeline.models import Recipe, Review
from llm_pipeline.pipeline import LLMAnalysisPipeline
from llm_pipeline.prompts import build_simple_prompt
from llm_pipeline.tweak_extractor import TweakExtractor

FIXTURES = json.loads((REPO / "src" / "llm_pipeline" / "fixtures" / "golden_tweaks.json")
                      .read_text(encoding="utf-8"))
T2 = next(f for f in FIXTURES["fixtures"] if f["tweak_id"] == "10813-t2")
COOKIES = json.loads((REPO / "data" / "recipe_10813_best-chocolate-chip-cookies.json")
                     .read_text(encoding="utf-8"))

# What a competent model returns for 10813-t2: one object per discrete
# modification, each with its own type and its own reasoning.
FIVE = {"modifications": [
    {"modification_type": "quantity_adjustment",
     "reasoning": "Halving the white sugar shifts the ratio toward brown sugar.",
     "edits": [{"target": "ingredients", "operation": "replace",
                "find": "1 cup white sugar", "replace": "0.5 cup white sugar"}]},
    {"modification_type": "quantity_adjustment",
     "reasoning": "More brown sugar gives a chewier, more flavourful cookie.",
     "edits": [{"target": "ingredients", "operation": "replace",
                "find": "1 cup packed brown sugar", "replace": "1.5 cups packed brown sugar"}]},
    {"modification_type": "removal",
     "reasoning": "Omitting the water reduces spread.",
     "edits": [{"target": "ingredients", "operation": "remove",
                "find": "2 teaspoons hot water"}]},
    {"modification_type": "addition",
     "reasoning": "Cream of tartar helps the cookies hold their shape.",
     "edits": [{"target": "ingredients", "operation": "add_after",
                "find": "0.5 teaspoon salt", "add": "1 teaspoon cream of tartar"}]},
    {"modification_type": "technique_change",
     "reasoning": "Chilling the dough stops it spreading when baked.",
     "edits": [{"target": "instructions", "operation": "replace",
                "find": "Drop spoonfuls of dough 2 inches apart onto ungreased baking sheets.",
                "replace": "Refrigerate the dough at least 1 hour, then drop spoonfuls of dough 2 inches apart onto ungreased baking sheets."}]},
]}


def stub_client(payload):
    def create(**kwargs):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=json.dumps(payload)))])
    return create


def recipe():
    return Recipe(recipe_id=COOKIES["recipe_id"], title=COOKIES["title"],
                  ingredients=COOKIES["ingredients"], instructions=COOKIES["instructions"])


def review():
    return Review(text=T2["review_text"], rating=T2["review_rating"],
                  has_modification=True, tweak_id="10813-t2", tweak_rank=2)


class TestExtractorReturnsEveryModification(unittest.TestCase):
    def setUp(self):
        self.ex = TweakExtractor()
        self.ex.client.chat.completions.create = stub_client(FIVE)

    def test_all_five_discrete_modifications_are_returned(self):
        mods = self.ex.extract_modifications(review(), recipe())
        self.assertEqual(len(mods), 5,
                         "a review describing five modifications must yield five")

    def test_each_modification_keeps_its_own_type(self):
        types_ = [m.modification_type for m in self.ex.extract_modifications(review(), recipe())]
        self.assertEqual(sorted(set(types_)),
                         ["addition", "quantity_adjustment", "removal", "technique_change"],
                         "all four categories must survive")

    def test_each_modification_keeps_its_own_reasoning(self):
        reasons = [m.reasoning for m in self.ex.extract_modifications(review(), recipe())]
        self.assertEqual(len(set(reasons)), 5, "each modification explains itself")
        self.assertTrue(any("cream of tartar" in r.lower() for r in reasons),
                        "the reasoning must be specific to its own change")

    def test_a_single_object_response_is_still_accepted(self):
        """Backward compatibility with the old one-object response shape."""
        self.ex.client.chat.completions.create = stub_client(FIVE["modifications"][0])
        mods = self.ex.extract_modifications(review(), recipe())
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0].modification_type, "quantity_adjustment")

    def test_an_empty_list_is_accepted_for_a_review_describing_no_change(self):
        """Fixtures 19117-t2 and 77935-t2 expect exactly this."""
        self.ex.client.chat.completions.create = stub_client({"modifications": []})
        self.assertEqual(self.ex.extract_modifications(review(), recipe()), [])


class TestPromptAsksForAllOfThem(unittest.TestCase):
    def test_the_prompt_requests_a_list_of_discrete_modifications(self):
        p = build_simple_prompt("r", "t", ["i"], ["s"])
        self.assertIn('"modifications"', p, "the prompt must ask for a list")
        low = p.lower()
        self.assertTrue("discrete" in low or "separate" in low,
                        "the prompt must say modifications are separated")

    def test_the_prompt_gives_the_worked_multi_modification_example(self):
        p = build_simple_prompt("r", "t", ["i"], ["s"])
        self.assertIn("halved the sugar", p.lower())


class TestPipelineCarriesEveryModification(unittest.TestCase):
    def test_one_review_becomes_several_attributed_modifications(self):
        with tempfile.TemporaryDirectory() as d:
            p = LLMAnalysisPipeline(output_dir=d)
            p.tweak_extractor.client.chat.completions.create = stub_client(FIVE)
            # Only tweak 2 is interesting here; the others return nothing.
            original = p.tweak_extractor.extract_modifications
            p.tweak_extractor.extract_modifications = (
                lambda rv, rc, **kw: original(rv, rc) if rv.tweak_id == "10813-t2" else [])
            result = p.process_single_recipe(
                str(REPO / "data" / "recipe_10813_best-chocolate-chip-cookies.json"))

            self.assertEqual(len(result.modifications_applied), 5,
                             "each discrete modification is its own attributed entry")
            self.assertTrue(all(m.source_tweak_id == "10813-t2"
                                for m in result.modifications_applied),
                            "all five cite the same source tweak")
            self.assertEqual(
                sorted(set(m.modification_type for m in result.modifications_applied)),
                ["addition", "quantity_adjustment", "removal", "technique_change"])

    def test_every_change_reaches_the_recipe(self):
        with tempfile.TemporaryDirectory() as d:
            p = LLMAnalysisPipeline(output_dir=d)
            p.tweak_extractor.client.chat.completions.create = stub_client(FIVE)
            original = p.tweak_extractor.extract_modifications
            p.tweak_extractor.extract_modifications = (
                lambda rv, rc, **kw: original(rv, rc) if rv.tweak_id == "10813-t2" else [])
            r = p.process_single_recipe(
                str(REPO / "data" / "recipe_10813_best-chocolate-chip-cookies.json"))
            self.assertIn("0.5 cup white sugar", r.ingredients)
            self.assertIn("1.5 cups packed brown sugar", r.ingredients)
            self.assertIn("1 teaspoon cream of tartar", r.ingredients)
            self.assertNotIn("2 teaspoons hot water", r.ingredients)
            self.assertTrue(any("Refrigerate the dough" in s for s in r.instructions),
                            "the technique change must reach the instructions")


if __name__ == "__main__":
    unittest.main(verbosity=2)
