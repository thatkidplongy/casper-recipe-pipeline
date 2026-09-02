"""
Step 1: Tweak Extraction & Parsing

This module extracts structured modifications from review text using LLM processing.
It converts natural language descriptions of recipe changes into structured
ModificationObject instances.
"""

import json
import os
from typing import Optional

from loguru import logger
from openai import OpenAI
from pydantic import ValidationError

from .models import ExtractionResult, ModificationObject, Recipe, Review
from .prompts import build_simple_prompt

# The model the README documents. Kept here so code and docs cannot drift: a
# test asserts this string appears in README.md.
DEFAULT_MODEL = "gpt-4o-mini"


class TweakExtractor:
    """Extracts structured modifications from review text using LLM processing."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize the TweakExtractor against any OpenAI-compatible endpoint.

        Nothing about this pipeline needs OpenAI specifically. Groq, Together,
        Fireworks, OpenRouter and a local server all speak the same API, so the
        endpoint and model are configuration rather than constants. That means
        the evaluation can be run without a funded OpenAI account.

        Resolution order for each setting is explicit argument, then
        environment, then default.

        Args:
            api_key: API key. Falls back to LLM_API_KEY, then OPENAI_API_KEY.
            model: Model id. Falls back to LLM_MODEL, then DEFAULT_MODEL.
            base_url: API base URL. Falls back to LLM_BASE_URL, then the
                OpenAI default. Point it at, for example,
                https://api.groq.com/openai/v1
        """
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_MODEL
        resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or None
        resolved_key = (
            api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        )

        client_kwargs = {"api_key": resolved_key}
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url

        self.client = OpenAI(**client_kwargs)

        logger.info(
            f"Initialized TweakExtractor with model: {self.model} "
            f"(endpoint: {resolved_base_url or 'OpenAI default'})"
        )

    def extract_modifications(
        self,
        review: Review,
        recipe: Recipe,
        max_retries: int = 2,
    ) -> list[ModificationObject]:
        """
        Extract every discrete modification a review describes.

        A review commonly describes several changes with different rationales,
        so this returns a list. An empty list is a valid, meaningful answer: it
        means the reviewer described no change they actually made.

        Args:
            review: Review object containing modification text
            recipe: Original recipe being modified
            max_retries: Number of retry attempts if parsing fails

        Returns:
            One ModificationObject per discrete modification, in the order the
            model returned them. Empty list if none, or if extraction failed.
        """
        if not review.has_modification:
            logger.warning("Review has no modification flag set")
            return []

        prompt = build_simple_prompt(
            review.text, recipe.title, recipe.ingredients, recipe.instructions
        )

        logger.debug(
            "Extracting modifications from review: {}...".format(review.text[:100])
        )

        raw_output = None
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.1,  # Low temperature for consistent extractions
                    max_tokens=2000,  # A multi-modification review needs headroom
                )

                raw_output = response.choices[0].message.content
                logger.debug(f"LLM raw output: {raw_output}")

                if not raw_output:
                    logger.warning(f"Attempt {attempt + 1}: Empty response from LLM")
                    continue

                modifications = self._parse(json.loads(raw_output))

                logger.info(
                    f"Extracted {len(modifications)} discrete modification(s): "
                    f"{[m.modification_type for m in modifications]}"
                )
                return modifications

            except json.JSONDecodeError as e:
                logger.warning(f"Attempt {attempt + 1}: Failed to parse JSON: {e}")
                if attempt == max_retries:
                    logger.error(f"Max retries reached. Raw output: {raw_output}")

            except ValidationError as e:
                logger.warning(f"Attempt {attempt + 1}: Validation error: {e}")
                if attempt == max_retries:
                    logger.error(f"Max retries reached. Raw output: {raw_output}")

            except Exception as e:
                logger.error(f"Attempt {attempt + 1}: Unexpected error: {e}")
                if attempt == max_retries:
                    return []

        return []

    @staticmethod
    def _parse(payload: dict) -> list[ModificationObject]:
        """Accept the list response, and a bare single object for compatibility.

        Earlier versions of this prompt returned one modification per review, and
        a model will still occasionally answer in that shape. Treating it as a
        one-element list is cheaper than a retry and loses nothing.
        """
        if "modifications" in payload:
            return ExtractionResult(**payload).modifications
        if "modification_type" in payload:
            return [ModificationObject(**payload)]
        return ExtractionResult(**payload).modifications

    def extract_all_modifications(
        self, reviews: list[Review], recipe: Recipe
    ) -> list[tuple[ModificationObject, Review]]:
        """Extract a modification from every review, in the order given.

        Deterministic by construction: no sampling, no shuffling. The caller
        supplies the ranking, and this walks it. A review that yields nothing is
        skipped and logged rather than aborting the recipe, so one bad
        extraction cannot discard the rest of the community's tweaks.

        Args:
            reviews: Reviews to process, already in ranked order
            recipe: Original recipe being modified

        Returns:
            List of (ModificationObject, source Review) in the input order
        """
        candidates = [r for r in reviews if r.has_modification]

        if not candidates:
            logger.warning("No reviews with modifications found")
            return []

        logger.info(f"Extracting from {len(candidates)} tweaks in ranked order")

        extracted = []
        for review in candidates:
            label = review.tweak_id or review.text[:40]
            modifications = self.extract_modifications(review, recipe)
            if modifications:
                extracted.extend((mod, review) for mod in modifications)
                logger.info(
                    f"Tweak {label}: {len(modifications)} discrete modification(s) "
                    f"{[m.modification_type for m in modifications]}"
                )
            else:
                logger.warning(
                    f"Tweak {label}: no modifications extracted "
                    f"(the reviewer may have described no change they made)"
                )

        return extracted

    def test_extraction(
        self, review_text: str, recipe_data: dict
    ) -> list[ModificationObject]:
        """
        Test extraction with raw text and recipe data.

        Args:
            review_text: Raw review text
            recipe_data: Raw recipe dictionary

        Returns:
            ModificationObject if successful
        """
        review = Review(text=review_text, has_modification=True)
        recipe = Recipe(
            recipe_id=recipe_data.get("recipe_id", "test"),
            title=recipe_data.get("title", "Test Recipe"),
            ingredients=recipe_data.get("ingredients", []),
            instructions=recipe_data.get("instructions", []),
        )

        return self.extract_modifications(review, recipe)
