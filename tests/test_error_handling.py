"""A failed call is not an empty answer.

`extract_modifications` returned `[]` for both "the model correctly extracted
nothing" and "every request errored". The harness could not tell them apart, so
a total outage scored the two zero-modification fixtures 2 out of 2 correct.

It also retried permanent errors. A 404 for a model that does not exist, or a
401 for a bad key, was retried three times per fixture with no inspection.

Run: .venv/bin/python tests/test_error_handling.py
"""

import json
import os
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("OPENAI_API_KEY", "test-value-never-sent-anywhere")

from loguru import logger
logger.remove()

import httpx
from openai import APIConnectionError, APIStatusError, RateLimitError

from llm_pipeline.models import Recipe, Review
from llm_pipeline.tweak_extractor import ExtractionError, TweakExtractor


def recipe():
    return Recipe(recipe_id="1", title="t", ingredients=["1 cup sugar"], instructions=["Bake."])


def review():
    return Review(text="I halved the sugar.", has_modification=True)


def status_error(code, body=None):
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(code, request=request, json=body or {"error": {"message": "nope"}})
    return APIStatusError("boom", response=response, body=body)


class Counting:
    """Records how many requests were attempted."""

    def __init__(self, raiser):
        self.calls = 0
        self.raiser = raiser

    def __call__(self, **kwargs):
        self.calls += 1
        raise self.raiser()


class TestPermanentErrorsAreNotRetried(unittest.TestCase):
    def _extractor(self, raiser):
        ex = TweakExtractor()
        self.counter = Counting(raiser)
        ex.client.chat.completions.create = self.counter
        return ex

    def test_a_404_is_not_retried(self):
        ex = self._extractor(lambda: status_error(404))
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe())
        self.assertEqual(self.counter.calls, 1, "a 404 must not be retried")

    def test_a_401_is_not_retried(self):
        ex = self._extractor(lambda: status_error(401))
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe())
        self.assertEqual(self.counter.calls, 1, "a 401 must not be retried")

    def test_insufficient_quota_is_not_retried(self):
        ex = self._extractor(lambda: status_error(
            429, {"error": {"code": "insufficient_quota", "message": "no credits"}}))
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe())
        self.assertEqual(self.counter.calls, 1,
                         "an exhausted balance is permanent, not a rate limit")


class TestTransientErrorsAreRetried(unittest.TestCase):
    def test_a_connection_error_is_retried_then_raises(self):
        ex = TweakExtractor()
        counter = Counting(lambda: APIConnectionError(
            request=httpx.Request("POST", "https://example.test/v1")))
        ex.client.chat.completions.create = counter
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe(), max_retries=2)
        self.assertEqual(counter.calls, 3, "transient failures deserve retries")


class TestAFailureIsNeverAnEmptyAnswer(unittest.TestCase):
    def test_a_failed_call_raises_rather_than_returning_empty(self):
        ex = TweakExtractor()
        ex.client.chat.completions.create = Counting(lambda: status_error(500))
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe(), max_retries=0)

    def test_unparseable_output_raises_rather_than_returning_empty(self):
        ex = TweakExtractor()
        def bad(**kwargs):
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content="not json at all"))])
        ex.client.chat.completions.create = bad
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe(), max_retries=0)

    def test_a_genuine_empty_extraction_returns_empty(self):
        """The two zero-modification fixtures depend on this staying true."""
        ex = TweakExtractor()
        def empty(**kwargs):
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps({"modifications": []})))])
        ex.client.chat.completions.create = empty
        self.assertEqual(ex.extract_modifications(review(), recipe()), [])


class TestOneBadReviewDoesNotDiscardTheRest(unittest.TestCase):
    def test_a_failing_review_is_logged_and_the_others_still_extract(self):
        ex = TweakExtractor()
        calls = {"n": 0}
        def sometimes(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise status_error(500)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps({"modifications": [
                    {"modification_type": "quantity_adjustment", "reasoning": "r",
                     "edits": [{"target": "ingredients", "operation": "replace",
                                "find": "1 cup sugar", "replace": "0.5 cup sugar"}]}]})))])
        ex.client.chat.completions.create = sometimes
        reviews = [Review(text="a", has_modification=True, tweak_id="x-t1", tweak_rank=1),
                   Review(text="b", has_modification=True, tweak_id="x-t2", tweak_rank=2)]
        got = ex.extract_all_modifications(reviews, recipe(), max_retries=0)
        self.assertEqual(len(got), 1, "the second review must still be extracted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
