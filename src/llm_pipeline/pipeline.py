"""
LLM Analysis Pipeline - Main Orchestrator

This module coordinates the complete 3-step pipeline:
1. Extract modifications from reviews
2. Apply modifications to recipes
3. Generate enhanced recipes with attribution

Processes recipe data from scraped JSON files and outputs enhanced recipes.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger

from .enhanced_recipe_generator import EnhancedRecipeGenerator
from .models import EnhancedRecipe, Recipe, Review
from .recipe_modifier import RecipeModifier
from .tweak_extractor import TweakExtractor


class LLMAnalysisPipeline:
    """Complete pipeline for analyzing recipes and generating enhanced versions."""

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        output_dir: str = "data/enhanced",
        pipeline_version: str = "1.0.0",
    ):
        """
        Initialize the complete LLM Analysis Pipeline.

        Args:
            openai_api_key: OpenAI API key (loads from env if not provided)
            output_dir: Directory to save enhanced recipes
            pipeline_version: Version identifier for tracking
        """
        # Load environment variables
        load_dotenv()

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize pipeline components
        self.tweak_extractor = TweakExtractor(api_key=openai_api_key)
        self.recipe_modifier = RecipeModifier()
        self.enhanced_generator = EnhancedRecipeGenerator(
            pipeline_version=pipeline_version
        )

        logger.info(f"Initialized LLM Analysis Pipeline v{pipeline_version}")
        logger.info(f"Output directory: {self.output_dir}")

    def load_recipe_data(self, file_path: str) -> Dict[str, Any]:
        """
        Load recipe data from JSON file.

        Args:
            file_path: Path to recipe JSON file

        Returns:
            Recipe data dictionary
        """
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def parse_recipe_data(self, recipe_data: Dict[str, Any]) -> Recipe:
        """
        Parse raw recipe data into Recipe object.

        Args:
            recipe_data: Raw recipe data from JSON

        Returns:
            Recipe object
        """
        return Recipe(
            recipe_id=recipe_data.get("recipe_id", "unknown"),
            title=recipe_data.get("title", "Unknown Recipe"),
            ingredients=recipe_data.get("ingredients", []),
            instructions=recipe_data.get("instructions", []),
            description=recipe_data.get("description"),
            servings=recipe_data.get("servings"),
            rating=recipe_data.get("rating"),
        )

    def parse_reviews_data(self, recipe_data: Dict[str, Any]) -> List[Review]:
        """
        Parse raw review data into Review objects.

        Args:
            recipe_data: Raw recipe data containing reviews

        Returns:
            List of Review objects
        """
        reviews = []
        raw_reviews = recipe_data.get("reviews", [])

        for review_data in raw_reviews:
            if review_data.get("text"):
                review = Review(
                    text=review_data["text"],
                    rating=review_data.get("rating"),
                    username=review_data.get("username"),
                    has_modification=review_data.get("has_modification", False),
                )
                reviews.append(review)

        return reviews

    def parse_featured_tweaks(self, recipe_data: Dict[str, Any]) -> List[Review]:
        """Parse the ranked featured-tweak list into Review objects.

        AllRecipes surfaces featured tweaks in its own order, and that order is
        the only ranking signal the scrape captures. It is used verbatim: rank 1
        is the first entry. When a recipe has no featured tweaks, the flagged
        reviews are used in file order so behaviour degrades rather than
        disappearing.

        Args:
            recipe_data: Raw recipe data from JSON

        Returns:
            Review objects in ranked order, each carrying its tweak id and rank
        """
        recipe_id = recipe_data.get("recipe_id", "unknown")
        raw = recipe_data.get("featured_tweaks") or []
        source = "featured_tweaks"

        if not raw:
            raw = [r for r in recipe_data.get("reviews", []) if r.get("has_modification")]
            source = "flagged reviews (no featured tweaks present)"

        tweaks = []
        for rank, item in enumerate(raw, start=1):
            if not item.get("text"):
                continue
            tweaks.append(
                Review(
                    text=item["text"],
                    rating=item.get("rating"),
                    username=item.get("username"),
                    has_modification=True,
                    tweak_id=f"{recipe_id}-t{rank}",
                    tweak_rank=rank,
                )
            )

        logger.info(f"Loaded {len(tweaks)} tweaks in ranked order from {source}")
        return tweaks

    def process_single_recipe(
        self, recipe_file: str, save_output: bool = True
    ) -> Optional[EnhancedRecipe]:
        """
        Process a single recipe through the complete pipeline.

        Args:
            recipe_file: Path to recipe JSON file
            save_output: Whether to save the enhanced recipe

        Returns:
            EnhancedRecipe if successful, None otherwise
        """
        try:
            logger.info(f"Processing recipe file: {recipe_file}")

            # Step 0: Load and parse data
            recipe_data = self.load_recipe_data(recipe_file)
            recipe = self.parse_recipe_data(recipe_data)
            tweaks = self.parse_featured_tweaks(recipe_data)

            logger.info(f"Loaded recipe: {recipe.title}")

            if not tweaks:
                logger.warning("No featured tweaks or flagged reviews found")
                return None

            # Step 1: Extract from every featured tweak, in ranked order
            logger.info("Step 1: Extracting from every featured tweak in rank order...")
            extractions = self.tweak_extractor.extract_all_modifications(tweaks, recipe)

            if not extractions:
                logger.warning("No modification could be extracted from any tweak")
                return None

            # Step 2: Apply each modification in rank order, highest first
            logger.info("Step 2: Applying modifications in rank order...")
            current_recipe = recipe
            applied = []

            for modification, source_review in extractions:
                current_recipe, change_records = self.recipe_modifier.apply_modification(
                    current_recipe, modification
                )
                if change_records:
                    applied.append((modification, source_review, change_records))
                else:
                    # Every edit from this tweak missed. Recording it would
                    # attribute a change to a reviewer that never reached the
                    # recipe, so the tweak is dropped and the reason logged.
                    logger.warning(
                        f"Tweak {source_review.tweak_id} changed nothing "
                        f"({len(modification.edits)} edits, none matched); not recorded"
                    )

            total = sum(len(records) for _, _, records in applied)
            logger.info(
                f"Applied {len(applied)} of {len(extractions)} tweaks, {total} changes"
            )

            if not applied:
                # No tweak reached the recipe. Publishing here would produce a
                # recipe identical to the original, titled "(Community
                # Enhanced)", carrying citations for changes that never
                # happened. A run that changed nothing is a failed run.
                logger.error(
                    f"No edits applied to '{recipe.title}': {len(extractions)} tweak(s) "
                    f"were extracted but none matched the recipe. Refusing to publish "
                    f"an unchanged recipe as enhanced."
                )
                return None

            # Step 3: Generate enhanced recipe with per-tweak attribution
            logger.info("Step 3: Generating enhanced recipe with attribution...")

            enhanced_recipe = self.enhanced_generator.generate_enhanced_recipe(
                recipe, current_recipe, applied
            )

            logger.info(f"Generated enhanced recipe: {enhanced_recipe.title}")

            # Save output
            if save_output:
                output_filename = f"enhanced_{recipe.recipe_id}_{recipe.title.lower().replace(' ', '-')[:30]}.json"
                output_path = self.output_dir / output_filename
                self.enhanced_generator.save_enhanced_recipe(
                    enhanced_recipe, str(output_path)
                )

            return enhanced_recipe

        except Exception as e:
            logger.error(f"Failed to process recipe {recipe_file}: {e}")
            import traceback

            traceback.print_exc()
            return None

    def process_recipe_directory(self, data_dir: str = "data") -> List[EnhancedRecipe]:
        """
        Process all recipe files in a directory.

        Args:
            data_dir: Directory containing recipe JSON files

        Returns:
            List of successfully processed EnhancedRecipe objects
        """
        data_path = Path(data_dir)
        recipe_files = list(data_path.glob("recipe_*.json"))

        logger.info(f"Found {len(recipe_files)} recipe files to process")

        enhanced_recipes = []
        for recipe_file in recipe_files:
            logger.info(f"\n{'=' * 60}")
            enhanced_recipe = self.process_single_recipe(str(recipe_file))

            if enhanced_recipe:
                enhanced_recipes.append(enhanced_recipe)
                logger.info(f"✓ Successfully processed: {enhanced_recipe.title}")
            else:
                logger.warning(f"✗ Failed to process: {recipe_file.name}")

        logger.info(f"\n{'=' * 60}")
        logger.info(
            f"Pipeline complete: {len(enhanced_recipes)}/{len(recipe_files)} recipes successfully enhanced"
        )

        return enhanced_recipes

    def generate_summary_report(
        self, enhanced_recipes: List[EnhancedRecipe]
    ) -> Dict[str, Any]:
        """
        Generate a summary report of pipeline results.

        Args:
            enhanced_recipes: List of enhanced recipes

        Returns:
            Summary report dictionary
        """
        if not enhanced_recipes:
            return {"status": "no_recipes_processed"}

        total_modifications = sum(
            len(recipe.modifications_applied) for recipe in enhanced_recipes
        )
        total_changes = sum(
            recipe.enhancement_summary.total_changes for recipe in enhanced_recipes
        )

        change_type_counts = {}
        for recipe in enhanced_recipes:
            for change_type in recipe.enhancement_summary.change_types:
                change_type_counts[change_type] = (
                    change_type_counts.get(change_type, 0) + 1
                )

        report = {
            "pipeline_summary": {
                "recipes_processed": len(enhanced_recipes),
                "total_modifications_applied": total_modifications,
                "total_changes_made": total_changes,
                "change_type_distribution": change_type_counts,
            },
            "enhanced_recipes": [
                {
                    "recipe_id": recipe.recipe_id,
                    "title": recipe.title,
                    "modifications_count": len(recipe.modifications_applied),
                    "changes_count": recipe.enhancement_summary.total_changes,
                    "change_types": recipe.enhancement_summary.change_types,
                }
                for recipe in enhanced_recipes
            ],
        }

        return report

    def save_summary_report(
        self, enhanced_recipes: List[EnhancedRecipe], output_path: Optional[str] = None
    ) -> str:
        """
        Save pipeline summary report to JSON file.

        Args:
            enhanced_recipes: List of enhanced recipes
            output_path: Path to save report (auto-generated if None)

        Returns:
            Path to saved report
        """
        if output_path is None:
            output_path = str(self.output_dir / "pipeline_summary_report.json")

        report = self.generate_summary_report(enhanced_recipes)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved pipeline summary report to: {output_path}")
        return output_path
