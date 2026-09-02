"""The extractor must run against any OpenAI-compatible endpoint.

Today TweakExtractor hardcodes gpt-3.5-turbo while the README documents
gpt-4o-mini, and the base URL is not configurable at all, so evaluating the
pipeline requires a funded OpenAI account specifically.

Run: .venv/bin/python tests/test_provider_config.py
"""

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("OPENAI_API_KEY", "test-value-never-sent-anywhere")

from loguru import logger
logger.remove()

from llm_pipeline.tweak_extractor import DEFAULT_MODEL, TweakExtractor


@contextmanager
def env(**kwargs):
    saved = {k: os.environ.get(k) for k in kwargs}
    try:
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestModelIsConfigurable(unittest.TestCase):
    def test_the_default_matches_the_documented_model(self):
        """The README documents gpt-4o-mini. The code used gpt-3.5-turbo."""
        readme = (REPO / "README.md").read_text()
        self.assertIn(DEFAULT_MODEL, readme,
                      f"default {DEFAULT_MODEL!r} is not the model the README documents")

    def test_the_model_comes_from_the_environment(self):
        with env(LLM_MODEL="llama-3.3-70b-versatile"):
            self.assertEqual(TweakExtractor().model, "llama-3.3-70b-versatile")

    def test_an_explicit_argument_beats_the_environment(self):
        with env(LLM_MODEL="from-env"):
            self.assertEqual(TweakExtractor(model="explicit").model, "explicit")

    def test_without_configuration_it_falls_back_to_the_default(self):
        with env(LLM_MODEL=None):
            self.assertEqual(TweakExtractor().model, DEFAULT_MODEL)


class TestEndpointIsConfigurable(unittest.TestCase):
    def test_the_base_url_comes_from_the_environment(self):
        with env(LLM_BASE_URL="https://api.groq.com/openai/v1"):
            ex = TweakExtractor()
            self.assertIn("api.groq.com", str(ex.client.base_url))

    def test_an_explicit_base_url_beats_the_environment(self):
        with env(LLM_BASE_URL="https://from-env.example/v1"):
            ex = TweakExtractor(base_url="https://explicit.example/v1")
            self.assertIn("explicit.example", str(ex.client.base_url))

    def test_no_base_url_leaves_the_openai_default(self):
        with env(LLM_BASE_URL=None):
            self.assertIn("api.openai.com", str(TweakExtractor().client.base_url))


class TestApiKeyResolution(unittest.TestCase):
    def test_a_provider_neutral_key_variable_is_accepted(self):
        with env(LLM_API_KEY="gsk_" + "x" * 44, OPENAI_API_KEY=None):
            self.assertTrue(TweakExtractor().client.api_key)

    def test_openai_api_key_still_works(self):
        with env(LLM_API_KEY=None, OPENAI_API_KEY="sk-" + "y" * 44):
            self.assertTrue(TweakExtractor().client.api_key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
