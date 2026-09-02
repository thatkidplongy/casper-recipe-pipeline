"""
Step 1: Tweak Extraction & Parsing

This module extracts structured modifications from review text using LLM processing.
It converts natural language descriptions of recipe changes into structured
ModificationObject instances.
"""

import json
import os
import re
import time
from typing import Optional

from loguru import logger
from openai import APIStatusError, OpenAI
from pydantic import ValidationError

from .models import ExtractionResult, ModificationObject, Recipe, Review
from .prompts import build_simple_prompt


class ExtractionError(RuntimeError):
    """No valid extraction could be obtained for a review.

    Distinct from an empty extraction. An empty list means the model read the
    review and correctly found no modification the reviewer actually made. This
    exception means the answer is unknown: the request failed, or no response
    could be parsed. Collapsing the two is how a total API outage scored as a
    set of correct empty answers.
    """


# HTTP statuses that will never succeed on retry: a missing model, a bad key, a
# malformed request. Retrying them burns time and quota for no chance of success.
FATAL_STATUSES = frozenset({400, 401, 403, 404, 422})

# Error codes that are permanent despite arriving on a retryable status. An
# exhausted balance returns 429, which looks like a rate limit and is not one.
# Codes that arrive on an otherwise fatal status but describe a failed
# generation rather than a bad request. The model failed to emit valid JSON;
# asking again can succeed, and in practice does.
RETRYABLE_CODES = frozenset({
    "json_validate_failed",
})

FATAL_CODES = frozenset({
    "insufficient_quota",
    "credit_balance_exhausted",
    "invalid_api_key",
    "model_not_found",
})

# Seconds before a single request is abandoned. The SDK default is a 600 second
# read timeout, which turns one unlucky request into a ten minute stall.
DEFAULT_TIMEOUT_SECONDS = 60.0

# Room for the hardest fixture. 10813-t2 decomposes into five discrete
# modifications and failed at 2000 tokens with "max completion tokens reached
# before generating a valid document".
MAX_OUTPUT_TOKENS = 4000

# How many times a single review may wait out a rate limit. Waiting is not a
# failed attempt at extraction, so it has its own budget; without a cap a
# throttled account would hang forever.
MAX_RATE_LIMIT_WAITS = 8

# Never sleep longer than this for one rate limit. A wait longer than a minute
# means the limit is a daily cap rather than a burst, and the run should end.
MAX_RATE_LIMIT_WAIT_SECONDS = 65.0

_RETRY_AFTER_IN_MESSAGE = re.compile(r"try again in ([0-9]*\.?[0-9]+)\s*s", re.I)


def retry_after_seconds(error: APIStatusError):
    """How long the API asked us to wait, or None if it did not say.

    Prefers the Retry-After header. Falls back to the wait stated in the error
    message, which is where Groq puts it. Guessing shorter than the stated wait
    guarantees the retry is rejected too.
    """
    response = getattr(error, "response", None)
    header = None
    if response is not None:
        try:
            header = response.headers.get("retry-after")
        except Exception:
            header = None
    if header:
        try:
            return float(header)
        except (TypeError, ValueError):
            pass

    body = getattr(error, "body", None)
    text = ""
    if isinstance(body, dict):
        detail = body.get("error", body)
        if isinstance(detail, dict):
            text = str(detail.get("message", ""))
    match = _RETRY_AFTER_IN_MESSAGE.search(text or str(error))
    return float(match.group(1)) if match else None


def _is_rate_limit(error: APIStatusError) -> bool:
    """A throttle we can wait out, as opposed to an exhausted balance."""
    return error.status_code == 429 and not _is_fatal(error)


def _resolve_timeout() -> float:
    """Per-request timeout in seconds, from LLM_TIMEOUT or the default.

    A malformed value falls back rather than raising: a bad timeout should not
    stop the pipeline from running.
    """
    raw = os.getenv("LLM_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            f"LLM_TIMEOUT={raw!r} is not a number; using {DEFAULT_TIMEOUT_SECONDS}s"
        )
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _error_code(error: APIStatusError):
    """The provider's error code, if it supplied one."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        detail = body.get("error", body)
        if isinstance(detail, dict):
            return detail.get("code")
    return None


def _is_fatal(error: APIStatusError) -> bool:
    """Is this error permanent, so that retrying cannot help?"""
    code = _error_code(error)
    if code in RETRYABLE_CODES:
        # A failed generation on a 4xx. The request was fine; the model was not.
        return False
    if error.status_code in FATAL_STATUSES:
        return True
    return code in FATAL_CODES


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

        client_kwargs = {
            "api_key": resolved_key,
            "timeout": _resolve_timeout(),
            # This class runs its own retry loop with backoff and with a check
            # for permanent errors. Leaving the SDK's default of 2 retries in
            # place nests three attempts inside each of ours: nine requests per
            # review, each able to hang until the timeout.
            "max_retries": 0,
        }
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url

        self.client = OpenAI(**client_kwargs)

        # The most recent raw model response, kept so a caller can record what
        # actually came back. A summary of a response is a claim about it; the
        # response itself is evidence.
        self.last_raw_output: Optional[str] = None

        # Report the client's own base_url rather than re-deriving it from the
        # environment. The SDK reads OPENAI_BASE_URL by itself, so a derived
        # value can claim "OpenAI default" while requests go elsewhere.
        logger.info(
            f"Initialized TweakExtractor with model: {self.model} "
            f"(endpoint: {self.endpoint}, "
            f"timeout: {client_kwargs['timeout']}s, sdk retries: 0)"
        )

    @property
    def endpoint(self) -> str:
        """The endpoint requests actually go to, as the client sees it."""
        return str(self.client.base_url)

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
        # Cleared up front so a failed call cannot leave the previous review's
        # response in place. A caller recording this per fixture would otherwise
        # attribute one review's output to another.
        self.last_raw_output = None

        if not review.has_modification:
            logger.warning("Review has no modification flag set")
            return []

        prompt = build_simple_prompt(
            review.text, recipe.title, recipe.ingredients, recipe.instructions
        )

        logger.debug(
            "Extracting modifications from review: {}...".format(review.text[:100])
        )

        last_error = None
        rate_limit_waits = 0
        attempt = 0
        while attempt <= max_retries:
            raw_output = None
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.1,  # Low temperature for consistent extractions
                    max_tokens=MAX_OUTPUT_TOKENS,
                )

                raw_output = response.choices[0].message.content
                self.last_raw_output = raw_output
                logger.debug(f"LLM raw output: {raw_output}")

                if not raw_output:
                    last_error = "empty response from the model"
                    logger.warning(f"Attempt {attempt + 1}: {last_error}")
                    continue

                modifications = self._parse(json.loads(raw_output))

                logger.info(
                    f"Extracted {len(modifications)} discrete modification(s): "
                    f"{[m.modification_type for m in modifications]}"
                )
                return modifications

            except APIStatusError as e:
                if _is_rate_limit(e):
                    wait = retry_after_seconds(e)
                    rate_limit_waits += 1
                    if wait is None or wait > MAX_RATE_LIMIT_WAIT_SECONDS \
                            or rate_limit_waits > MAX_RATE_LIMIT_WAITS:
                        raise ExtractionError(
                            f"rate limited and unable to wait it out "
                            f"({rate_limit_waits} wait(s), last asked for {wait}s): {e}"
                        ) from e
                    # Waiting out a throttle is not a failed extraction attempt,
                    # so it does not consume the retry budget. A tenth of a
                    # second of slack absorbs clock skew.
                    logger.info(
                        f"Rate limited; waiting {wait:.1f}s as instructed "
                        f"(wait {rate_limit_waits}/{MAX_RATE_LIMIT_WAITS})"
                    )
                    time.sleep(wait + 0.1)
                    continue

                if _is_fatal(e):
                    # Permanent. Retrying a missing model or a bad key wastes
                    # time and tells us nothing new, which is exactly what the
                    # original code did nine times per review.
                    raise ExtractionError(
                        f"permanent API error {e.status_code}, not retrying: {e}"
                    ) from e
                last_error = f"API error {e.status_code}: {e}"
                logger.warning(f"Attempt {attempt + 1}: {last_error}")

            except json.JSONDecodeError as e:
                last_error = f"response was not valid JSON: {e}"
                logger.warning(f"Attempt {attempt + 1}: {last_error}")

            except ValidationError as e:
                last_error = f"response did not match the schema: {e}"
                logger.warning(f"Attempt {attempt + 1}: {last_error}")

            except Exception as e:  # transport errors, timeouts, anything else
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"Attempt {attempt + 1}: {last_error}")

            if attempt < max_retries:
                backoff = 2**attempt
                logger.debug(f"Retrying in {backoff}s")
                time.sleep(backoff)
            attempt += 1

        # Every attempt failed. This is not an empty extraction: the answer is
        # unknown, and the caller must be able to tell the difference.
        raise ExtractionError(
            f"no valid extraction after {max_retries + 1} attempt(s); "
            f"last failure was {last_error}"
        )

    @staticmethod
    def _parse(payload: dict) -> list[ModificationObject]:
        """Accept the list response, and a bare single object for compatibility.

        Earlier versions of this prompt returned one modification per review, and
        a model will still occasionally answer in that shape. Treating it as a
        one-element list is cheaper than a retry and loses nothing.

        Entries that fail validation are skipped rather than discarding the
        response. A model returned a stray empty string inside an otherwise
        correct array, and rejecting the whole thing lost two good modifications
        because of one bad element. Skipping is logged, and a response whose
        every entry is invalid still raises.
        """
        if "modifications" not in payload and "modification_type" in payload:
            return [ModificationObject(**payload)]

        entries = payload.get("modifications", [])
        if not isinstance(entries, list):
            raise ValidationError.from_exception_data("ExtractionResult", [])

        modifications, skipped = [], 0
        for entry in entries:
            if not isinstance(entry, dict):
                skipped += 1
                logger.warning(f"Skipping non-object entry in modifications: {entry!r}")
                continue
            try:
                modifications.append(ModificationObject(**entry))
            except ValidationError as e:
                skipped += 1
                logger.warning(f"Skipping malformed modification: {e.error_count()} error(s)")

        if skipped and not modifications:
            raise ValidationError.from_exception_data("ExtractionResult", [])
        if skipped:
            logger.warning(
                f"Kept {len(modifications)} modification(s), skipped {skipped} malformed"
            )
        return modifications

    def extract_all_modifications(
        self, reviews: list[Review], recipe: Recipe, max_retries: int = 2
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
            try:
                modifications = self.extract_modifications(
                    review, recipe, max_retries=max_retries
                )
            except ExtractionError as e:
                # Recipe-level resilience: one unanswerable review must not
                # discard the rest of the community's tweaks. Logged as an
                # error, not a warning, because it is not "no modifications".
                logger.error(f"Tweak {label}: extraction failed, skipping. {e}")
                continue

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
