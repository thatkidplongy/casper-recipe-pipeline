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


class TestRateLimitsHonourRetryAfter(unittest.TestCase):
    """Groq's free tier is 8,000 tokens per minute and each request is ~2,700,
    so roughly three fit per minute. The API states the wait; backing off 1s and
    2s against a stated 7.5s guarantees the retry fails too."""

    def _rate_limited(self, headers=None, message="Please try again in 7.455s"):
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        response = httpx.Response(
            429, request=request, headers=headers or {},
            json={"error": {"message": message, "code": "rate_limit_exceeded"}})
        return APIStatusError("rate limited", response=response,
                              body={"error": {"message": message,
                                              "code": "rate_limit_exceeded"}})

    def test_the_retry_after_header_is_used(self):
        from llm_pipeline.tweak_extractor import retry_after_seconds
        self.assertEqual(retry_after_seconds(self._rate_limited({"retry-after": "9"})), 9.0)

    def test_the_wait_is_parsed_from_the_message_when_no_header(self):
        from llm_pipeline.tweak_extractor import retry_after_seconds
        self.assertAlmostEqual(retry_after_seconds(self._rate_limited()), 7.455, places=2)

    def test_an_unparseable_wait_returns_none(self):
        from llm_pipeline.tweak_extractor import retry_after_seconds
        self.assertIsNone(retry_after_seconds(self._rate_limited(message="slow down")))

    def test_a_rate_limit_does_not_consume_the_parse_retry_budget(self):
        """Waiting out a rate limit is not a failed attempt at extraction."""
        ex = TweakExtractor()
        calls = {"n": 0}
        def limited_then_ok(**kwargs):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise self._rate_limited({"retry-after": "0"})
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps({"modifications": []})))])
        ex.client.chat.completions.create = limited_then_ok
        # max_retries=0 means no parse retries, yet the rate limits must be ridden out.
        self.assertEqual(ex.extract_modifications(review(), recipe(), max_retries=0), [])
        self.assertEqual(calls["n"], 4)

    def test_rate_limit_waiting_is_bounded(self):
        ex = TweakExtractor()
        calls = {"n": 0}
        def always_limited(**kwargs):
            calls["n"] += 1
            raise self._rate_limited({"retry-after": "0"})
        ex.client.chat.completions.create = always_limited
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe(), max_retries=0)
        self.assertLessEqual(calls["n"], 10, "it must give up rather than wait forever")


class TestMalformedEntriesDoNotDiscardTheWholeExtraction(unittest.TestCase):
    """The model returned a stray "" inside the modifications array. Rejecting
    the whole response lost two valid modifications because of one bad entry."""

    def test_valid_modifications_survive_a_stray_entry(self):
        ex = TweakExtractor()
        payload = {"modifications": [
            {"modification_type": "quantity_adjustment", "reasoning": "r",
             "edits": [{"target": "ingredients", "operation": "replace",
                        "find": "1 cup sugar", "replace": "0.5 cup sugar"}]},
            "",
            {"modification_type": "addition", "reasoning": "r2",
             "edits": [{"target": "ingredients", "operation": "add_after",
                        "find": "1 cup sugar", "add": "1 pinch salt"}]},
        ]}
        def bad(**kwargs):
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps(payload)))])
        ex.client.chat.completions.create = bad
        got = ex.extract_modifications(review(), recipe(), max_retries=0)
        self.assertEqual(len(got), 2, "one malformed entry must not discard the valid ones")

    def test_an_entry_failing_the_schema_is_skipped_not_fatal(self):
        ex = TweakExtractor()
        payload = {"modifications": [
            {"modification_type": "not_a_real_category", "reasoning": "r", "edits": []},
            {"modification_type": "removal", "reasoning": "r",
             "edits": [{"target": "ingredients", "operation": "remove", "find": "1 cup sugar"}]},
        ]}
        def bad(**kwargs):
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps(payload)))])
        ex.client.chat.completions.create = bad
        got = ex.extract_modifications(review(), recipe(), max_retries=0)
        self.assertEqual(len(got), 1)

    def test_all_entries_invalid_still_raises(self):
        ex = TweakExtractor()
        def bad(**kwargs):
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps({"modifications": ["", 3]})))])
        ex.client.chat.completions.create = bad
        with self.assertRaises(ExtractionError):
            ex.extract_modifications(review(), recipe(), max_retries=0)


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
