# Recipe Enhancement Platform

Automatically enhances recipes by analyzing and applying community-tested modifications from AllRecipes.com. Uses LLM processing to extract meaningful recipe tweaks and apply them with full citation tracking.

## Installation

This project uses [`uv`](https://docs.astral.sh/uv/) for fast, reliable Python package management.

### Prerequisites

- Python 3.13+
- `uv` package manager

## Setup

```bash
uv sync
```

Use `uv sync`, not `uv pip sync pyproject.toml`. The latter installs the seven
declared dependencies and drops every transitive one, leaving `pydantic` and
`openai` unimportable.

### Environment Variables

Create a `.env` file in the project root. The pipeline speaks the OpenAI API but
is not tied to OpenAI: any OpenAI-compatible endpoint works, so the evaluation
can be run without a funded OpenAI account.

```env
# Required. LLM_API_KEY is checked first, then OPENAI_API_KEY.
OPENAI_API_KEY=your-api-key-here

# Optional. Defaults shown.
LLM_MODEL=gpt-4o-mini
# LLM_BASE_URL=                      # unset means the OpenAI default endpoint
```

Against Groq, for example:

```env
LLM_API_KEY=gsk_your-groq-key
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
```

Resolution order for each setting is explicit argument, then environment, then
default. The default model id is `gpt-4o-mini`, and a test asserts that this
README and the code agree on it.

## Usage

### 1. Scrape Recipes (Optional - data already provided)

```bash
uv run python src/scraper_v2.py
```

### 2. Run Recipe Enhancement Pipeline

```bash
cd src

# Test single recipe (chocolate chip cookies)
uv run python test_pipeline.py single

# Process all recipes
uv run python test_pipeline.py all
```

## Output

### Enhanced Recipes

Enhanced recipes are saved in `src/data/enhanced/`:

- `enhanced_[recipe_id]_[recipe-name].json` - Individual enhanced recipes with modifications applied
- `pipeline_summary_report.json` - Summary of all processing results

### Data Structure

Original scraped recipes in `data/` directory contain reviews with `has_modification: true` flags. Enhanced recipes include:

```json
{
  "recipe_id": "10813_enhanced",
  "title": "Best Chocolate Chip Cookies (Community Enhanced)",
  "ingredients": ["1 cup butter", "1 additional egg yolk", ...],
  "modifications_applied": [
    {
      "source_review": {
        "text": "I added an extra egg yolk for chewier texture",
        "rating": 5
      },
      "modification_type": "addition",
      "reasoning": "Improves texture and chewiness",
      "changes_made": [...]
    }
  ],
  "enhancement_summary": {
    "total_changes": 1,
    "change_types": ["addition"],
    "expected_impact": "Chewier texture and improved consistency"
  }
}
```

## How It Works

The LLM Analysis Pipeline processes recipes in 3 steps:

1. **Tweak Extraction**: Walks the scraped `featured_tweaks` list in ranked order and extracts *every* discrete modification each one describes. A review saying "I added an egg and halved the sugar" yields two modifications, each with its own category and rationale.
2. **Recipe Modification**: Applies changes using fuzzy line matching. A replace that alters no text is not recorded as a change.
3. **Enhanced Recipe Generation**: Creates the enhanced version with per-tweak attribution, so every change names the tweak it came from.

Selection is deterministic: the same input produces the same output. A run in
which no edit applied fails rather than publishing an unchanged recipe.

## Evaluation

`src/llm_pipeline/fixtures/golden_tweaks.json` holds the 12 featured tweaks
hand-labelled into 28 discrete modifications. To measure extraction:

```bash
uv run python scripts/run_golden_set.py --runs 10
uv run python scripts/run_golden_set.py --runs 10 --stub   # no network, control
```

See `docs/WRITEUP.md` for findings and `docs/pipeline-audit.md` for the full audit.

## Development

```bash
# Add dependencies
uv add <package_name>

# Run tests
cd src && uv run python test_pipeline.py single
```
