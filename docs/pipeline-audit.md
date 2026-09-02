# Recipe Enhancement Pipeline — Engineering Audit

Read-only audit of the inherited MVP. No source files were changed.
Scope: `src/scraper_v2.py`, `src/llm_pipeline/*`, `src/test_pipeline.py`, `data/`.

Every claim below is backed by either a line reference or a reproducible check run against the
committed sample data. Items I could not verify are listed under **Open questions** rather than
asserted.

---

## 1. Verdict

The pipeline runs, and on a narrow happy path it produces a plausible-looking enhanced recipe.
It does not do what the brief describes, and the gap is structural rather than cosmetic.

Three findings decide this:

1. **The ranking signal does not exist in the data.** The brief is built on "highest voted
   community-tested modifications". Nothing in `data/` carries a vote, helpful, or upvote count.
   The scraper explicitly abandoned it (`src/scraper_v2.py:259`, comment: *"Take the tweaks as-is
   without sorting by helpful count"*). Selection is `random.choice`
   (`src/llm_pipeline/tweak_extractor.py:136`).
2. **One review per recipe, one modification per review.** `ModificationObject`
   (`src/llm_pipeline/models.py:32`) carries a single `modification_type` and a single `reasoning`.
   The pipeline extracts from exactly one randomly chosen review
   (`src/llm_pipeline/pipeline.py:148`). Of 12 flagged reviews in the sample corpus, 9 describe
   more than one discrete modification.
3. **The committed sample outputs cannot be reproduced by the committed code.** Both files in
   `data/enhanced/` contain a `confidence_score` field and two `modifications_applied` entries.
   No `confidence_score` exists anywhere in `src/`, and `generate_enhanced_recipe` hardcodes a
   single-element list (`src/llm_pipeline/enhanced_recipe_generator.py:138`). The artifacts that
   demonstrate the product were produced by a version of the code that is no longer in the repo.

---

## 1a. The headline finding: the output is untruthful in both directions

Two defects, verified against the committed artifacts and against real runs, that
together describe the product's core problem. The system's only job is to tell a
user which community suggestion was applied and why. It gets that wrong in both
possible directions.

### It fabricates changes nobody suggested

Fixture `77935-t2` is this review, in full:

> It was amazing!! The portion is only enough for 2-3 people. **Very good as is**
> but **when I make it again I will use fresh ginger** to give it more of a
> ginger flavour.

The reviewer states plainly that they changed nothing and describes an intention
for next time. The correct extraction is empty.

The committed output at `data/enhanced/enhanced_77935_creamy-sweet-potato-with-ginge.json`
applies it anyway. It substitutes fresh ginger for ground, records the change,
cites this reviewer as the source, and states the improvement as fact:

```json
{
  "modification_type": "ingredient_substitution",
  "reasoning": "Using fresh ginger enhances the ginger flavor in the soup.",
  "changes_made": [{ "type": "ingredient",
                     "from_text": "1.5 teaspoons ground ginger",
                     "to_text": "1.5 teaspoons fresh ginger",
                     "operation": "replace" }]
}
```

Nobody made that change. The platform invented a community-tested modification
from a review whose entire point was that the recipe needed no modification.

### It claims changes it never made

The mirror image, reproduced across eight full runs. In four of eight, Spicy
Apple Cake is published with ingredients and instructions **byte-identical to the
original**, titled "Spicy Apple Cake (Community Enhanced)", with:

| Field | Value |
| --- | --- |
| `total_changes` | 0 |
| `changes_made` | `[]` |
| `change_types` | `["quantity_adjustment"]` |
| `expected_impact` | `"More apple chunks."` |

The citation names a reviewer who wrote that more apple is *"just my preference"*
and made no change. The stated impact describes an improvement present nowhere in
the file. The cause is a paraphrased `find` of "2 cups apple" scoring 0.53
against `2 cups apple - peeled, cored, and chopped`, under the 0.6 threshold, so
the only edit was dropped and Step 3 built the record regardless.

### Why they are one finding

A user cannot tell any of these apart from a correct result. There is no field
that distinguishes *applied*, *attempted and missed*, and *invented*. The
line-level diff the product promises is the feature, and in both cases it renders
a confident, well-formatted explanation of something that did not happen.

Fixing this is not a modelling problem. Refusing to emit a `ChangeRecord` when
the text did not change, and refusing to publish when zero edits applied, turns
both into loud failures. Both are small changes in `recipe_modifier.py` and
`pipeline.py`. Everything else in this audit is secondary to them.

---

## 2. Module map

| Module | Responsibility | State |
| --- | --- | --- |
| `src/scraper_v2.py` | Fetch AllRecipes HTML, parse JSON-LD recipe body, regex-classify reviews, write `data/recipe_*.json` | Works; loses the vote signal and most reviews |
| `src/llm_pipeline/models.py` | Pydantic schema for edits, modifications, enhanced recipes | Schema shape is the main design constraint |
| `src/llm_pipeline/prompts.py` | System prompt, extraction prompt, few-shot examples, two builders | Half of it is dead; one builder raises |
| `src/llm_pipeline/tweak_extractor.py` | Step 1. Pick a review, call OpenAI, parse and validate JSON | Runs; selection and retry logic are the weak points |
| `src/llm_pipeline/recipe_modifier.py` | Step 2. Fuzzy-locate target lines, apply edits, emit change records | Contains the highest-severity correctness bug |
| `src/llm_pipeline/enhanced_recipe_generator.py` | Step 3. Assemble enhanced recipe with attribution, serialize | Drops metadata; single-modification only |
| `src/llm_pipeline/pipeline.py` | Orchestrator, file IO, summary report | Fails a whole recipe on any single failure |
| `src/test_pipeline.py` | Manual demo runner (`single` / `all`) | Not a test; requires network and a live API key |

### Data flow

```
AllRecipes URL
  └─ scrape_allrecipes()                     scraper_v2.py:116
       ├─ JSON-LD → title/ingredients/instructions/nutrition/times
       ├─ photo-dialog reviews → featured_tweaks[]      (populated, then never read again)
       └─ ugc-review blocks  → reviews[]  + has_modification regex flag
  └─ data/recipe_<id>_<slug>.json

data/recipe_*.json
  └─ LLMAnalysisPipeline.process_single_recipe()        pipeline.py:116
       ├─ parse_recipe_data()   → Recipe        (drops prep/cook/total time, nutrition, url)
       ├─ parse_reviews_data()  → List[Review]  (drops date, is_featured)
       ├─ Step 1 TweakExtractor.extract_single_modification()
       │     └─ random.choice(reviews where has_modification) → 1 review
       │     └─ build_simple_prompt() → OpenAI chat → JSON → ModificationObject
       ├─ Step 2 RecipeModifier.apply_modification()
       │     └─ per edit: SequenceMatcher best line → str.replace / insert / pop
       │     └─ emits ChangeRecord per edit
       └─ Step 3 EnhancedRecipeGenerator.generate_enhanced_recipe()
             └─ wraps in EnhancedRecipe + SourceReview citation
  └─ <output_dir>/enhanced_<id>_<slug>.json
```

`output_dir` defaults to the relative path `data/enhanced` (`pipeline.py:31`). The documented
invocation is `cd src` first, so real runs write to `src/data/enhanced/`. Both directories are
committed with different content and different schemas.

---

## 3. Assumptions the code makes that the brief does not support

Ordered by product impact. Severity is my judgement of what breaks for a user.

### A1. "The highest voted tweak" — no vote data exists, selection is random
**Severity: critical. Confidence: certain.**

`extract_single_modification` picks `random.choice(modification_reviews)`
(`tweak_extractor.py:136`). There is no seed, so two runs of the same recipe produce different
enhanced recipes with no way to diff them. No review record in `data/` has a helpful or vote
count. `scraper_v2.py:259` documents the decision to drop sorting.

`featured_tweaks` is scraped and populated for 4 of 6 recipes (4, 1, 2, 0, 0, 5 entries), and the
pipeline never reads the key. The one Featured-Tweak signal that was captured is discarded in
favour of the generic review list.

### A2. "One review contains one modification" — 9 of 12 flagged reviews contain several
**Severity: critical. Confidence: certain.**

`ModificationObject` has one `modification_type` and one `reasoning` for a list of edits. A review
saying *"I added an egg and halved the sugar"* is two discrete modifications with two rationales.
The schema can hold both edits but must label them with a single type and a single explanation,
which is precisely the per-line "which suggestion and why" the product promises.

The clearest evidence is in the committed output. This review lists four numbered tweaks:

> (1) half cup of sugar and one-and-a-half cups of brown sugar; (2) I omitted the water;
> (3) I added a teaspoon of cream of tartar; (4) I refrigerated the batter for at least an hour

The enhanced recipe captured the sugar change only. The omitted water, the cream of tartar, and
the refrigeration step were all dropped, with no record that anything was missed.

Counted by hand across the corpus:

| Metric | Count |
| --- | --- |
| Reviews in `data/` | 19 |
| Flagged `has_modification` | 12 |
| Flagged reviews describing 2+ discrete modifications | 9 |
| Flagged reviews describing 0 applied modifications | 2 |
| Discrete modifications present in the corpus | ~30 |
| Modifications the pipeline can apply per recipe | 1 review's worth |

### A3. "A fuzzy line match means the edit applied" — it does not, and a false change is recorded
**Severity: critical. Confidence: certain, reproduced.**

`recipe_modifier.py:87-99` finds the best-matching line by `SequenceMatcher` over the whole
string, then performs an **exact substring** `original_text.replace(edit.find, edit.replace)`.
When the fuzzy match succeeds but the exact substring is absent, `replace` is a no-op, and the
code still appends a `ChangeRecord` claiming the line changed.

Reproduced against the real cookie ingredient list:

| `find` | Fuzzy match | Score | Text actually changed |
| --- | --- | --- | --- |
| `1 cup white sugar` | `1 cup white sugar` | 1.00 | yes |
| `1 cup sugar` | `1 cup white sugar` | 0.79 | **no** |
| `white sugar, 1 cup` | `1 cup white sugar` | 0.63 | **no** |

Rows two and three each emit a `ChangeRecord` with `from_text == to_text`. Downstream,
`total_changes` counts it, `expected_impact` narrates it, and the UI would render a diff line that
did not happen. The system tells the user a community tweak was applied when it was not.

### A4. Instruction-targeted edits can essentially never match
**Severity: high. Confidence: certain, reproduced.**

The similarity threshold is 0.6 over entire strings (`recipe_modifier.py:25,54`). Instruction
lines are long sentences; a precise `find` is short. The ratio is dominated by length difference.

The prompt's own few-shot examples (`prompts.py:144-151`) teach the model to emit exactly these
`find` values. Every one of them fails against the real recipe:

| `find` taught by the prompt | Best score vs. instructions | Result |
| --- | --- | --- |
| `350 degrees F` | 0.20 | no match |
| `about 10 minutes` | 0.35 | no match |
| `Preheat the oven to 350 degrees F (175 degrees C)` | 0.51 | no match |

The prompt and the matcher are specified against each other. `technique_change` is one of five
declared modification categories and is effectively dead on arrival, so temperature, time, and
method tweaks silently vanish. Community tweaks are heavily weighted toward exactly these.

### A5. `has_modification` is a loose regex that both over- and under-fires
**Severity: high. Confidence: certain.**

`scraper_v2.py:79-90`. Five patterns, any one match sets the flag. Two of the patterns match
intent and preference rather than an applied change:

- `(next time|will make again|definitely make)`
- `(more|less|extra) ([\w\s]+)`

**False positives that reach the LLM.** *"when I make it again I will use fresh ginger"* was
flagged, extracted, and applied in the committed output, substituting fresh ginger for ground.
That is an untested intention presented to the user as a community-tested modification.
*"I would prefer some more apple chunks in it, but that is just my preference"* is also flagged
and contains no change at all.

**False negatives that are dropped before the LLM sees them.** The `I (added|used|...)` pattern
requires the pronoun immediately before the verb:

- *"Also, I threw in carrots since i had them around"* — a real addition, not flagged.
- *"I forgot to get ground ginger and used minced"* — a real substitution, not flagged.

The gate is enforced twice: `extract_modification` refuses any review without the flag
(`tweak_extractor.py:53`), and `process_single_recipe` aborts the recipe when no review carries it
(`pipeline.py:142`). A scraper-side regex therefore decides what the LLM is ever allowed to read.

### A6. The corpus is far smaller than the ratings imply, and two recipes yield nothing
**Severity: high. Confidence: high.**

`requests.get` with no JavaScript execution (`scraper_v2.py:131`) captures only server-rendered
markup. AllRecipes paginates and lazily loads reviews.

| Recipe | Reviews scraped | Ratings on site |
| --- | --- | --- |
| Best Chocolate Chip Cookies | 9 | 19,353 |
| Creamy Sweet Potato Soup | 6 | 87 |
| Spicy Apple Cake | 2 | 79 |
| Nikujaga | 2 | 20 |
| Mango Teriyaki Marinade | 0 | 12 |
| Spiced Purple Plum Jam | 0 | none parsed |

Two of five recipes return `None` from the pipeline. Best case for `test_pipeline.py all` is 3 of
5, and no summary report distinguishes "no reviews scraped" from "extraction failed". Selecting a
*highest voted* tweak from 9 of 19,353 reviews is not a sampling problem, it is a different
product.

### A7. Failure is silent and indistinguishable from success
**Severity: high. Confidence: certain.**

If every edit fails to match, `change_records` is empty, and Step 3 still builds and saves an
enhanced recipe. `total_changes` is 0 while `expected_impact` carries the LLM's confident prose
about improvements that were never applied. The file is written, the run is reported as a
success, and nothing flags it.

`process_single_recipe` wraps everything in a bare `except Exception` returning `None`
(`pipeline.py:189`), so a missing API key, a rate limit, a malformed file, and a genuine
no-modifications case are all the same outcome to the caller.

### A8. Attribution is thinner than the product needs
**Severity: medium. Confidence: certain.**

`SourceReview.reviewer` is `None` in every committed output. The scraper's username selectors
(`scraper_v2.py:57-67`) match nothing in current markup, and no review record in `data/` has a
username. There is no review date, no permalink, no vote count. A citation that names no one and
links nowhere does not support "inspect which community suggestion was applied".

`prep_time`, `cook_time`, and `total_time` are always `None` in output. `Recipe`
(`models.py:125`) declares no such fields, so the `getattr` defaults in
`enhanced_recipe_generator.py:158-160` can never resolve. The scraper writes them as `preptime`
and `cooktime`, so the keys would not line up even if the fields existed.

### A9. Nutrition and servings are silently invalidated
**Severity: medium. Confidence: high.**

Halving the sugar does not change `nutrition` or `servings`. Nutrition is scraped and then not
carried into the enhanced recipe at all; `servings` is copied verbatim from the original. One
source review states the modification yielded 16 cookies instead of 48. Publishing an enhanced
recipe alongside the original recipe's nutrition facts is a correctness problem with a consumer
safety edge, and allergen-relevant substitutions run through the same path with no checks.

### A10. No line-level diff artifact is produced
**Severity: medium. Confidence: certain.**

The brief promises line-level diffs. `generate_comparison_data`
(`enhanced_recipe_generator.py:172`) builds exactly that structure and is never called or
persisted. The saved enhanced recipe holds only post-change content, so a UI must re-read the
source file to diff. `ChangeRecord` for an addition records `from_text: ""` and no index, so the
insertion point is not recoverable from the artifact.

### A11. Dead code and a broken prompt path
**Severity: medium. Confidence: certain, reproduced.**

`EXTRACTION_PROMPT` has an unbalanced brace at `prompts.py:53`. Calling `.format` on it raises
`ValueError: Single '}' encountered in format string`. That makes `build_few_shot_prompt` raise
for any input. `tweak_extractor.py:57` documents the workaround rather than the fix:
*"use simple prompt to avoid format string issues"*.

The consequence is that `FEW_SHOT_EXAMPLES` never reaches the model. All extraction is zero-shot,
and the examples that encode the intended output style are dead weight.

Also never called: `validate_modification_safety` (`recipe_modifier.py:221`), which is the only
guard against unmatched or low-similarity edits and would have caught A3;
`apply_modifications_batch` (`recipe_modifier.py:192`), the only multi-modification path;
`generate_comparison_data`; and `TweakExtractor.test_extraction`.

### A12. Model, cost, and reliability assumptions
**Severity: medium. Confidence: certain for the code, unverified at runtime.**

- The README documents GPT-4o-mini. The default is `gpt-3.5-turbo`
  (`tweak_extractor.py:24`) and nothing overrides it, so the documented model is not the one that runs.
- `max_tokens=1000` (`tweak_extractor.py:73`) caps output. A review with many edits truncates
  mid-JSON, fails to parse, and burns retries.
- Three attempts with an identical prompt at `temperature=0.1` and no backoff. A parse failure
  retries into the same failure; a 429 burns all attempts immediately and drops the recipe.
- No structured outputs or schema enforcement. Any value outside a `Literal` fails Pydantic
  validation and discards the whole review, with no repair step.
- Serial, one call per recipe, no concurrency, no caching, no cost tracking, no run manifest.
  Fine for 5 recipes; it is the whole scaling story for 10,000.

### A13. Repository and reproducibility hygiene
**Severity: low, but it is why the state is hard to trust.**

- `data/enhanced/` and `src/data/enhanced/` hold the same recipe under two different schemas. No
  source of truth.
- Output filenames are `title.lower().replace(' ', '-')[:30]` (`pipeline.py:181`). Only spaces are
  handled, so a title containing `/` writes into an unintended path, and truncation at 30
  characters can collide. The committed `...with-ginge.json` shows the mid-word cut.
- `test_pipeline.py` hardcodes `../data` and only runs correctly from `src/`. `single` mode is
  hardcoded to the cookie recipe.
- No pytest, no fixtures, no offline tests, no lint config, no CI. Nothing can be verified without
  network access and a funded API key.
- `scraper_v2.py:main()` rescrapes and overwrites `data/` on every run, with no politeness delay,
  no robots.txt check, and no raw HTML cache. Following the README's step 1 destroys the sample
  corpus that the rest of the work depends on.
- `docs/react_optimizor.md` and `docs/pull_request_template.md` are boilerplate from an unrelated
  React project, referencing Vercel previews and database migrations.
- `pyproject.toml` is still named `new-assignment` with a placeholder description.

---

## 4. What is genuinely sound

Worth keeping rather than rewriting:

- The three-step decomposition (extract, apply, attribute) is the right shape, and the seam
  between LLM reasoning and deterministic text application is the correct place to cut.
- Making the LLM emit structured edit operations rather than a rewritten recipe is the key design
  decision, and it is right. It is what makes line-level diffs and citation possible at all.
- Pydantic validation at the LLM boundary is the correct instinct.
- JSON-LD as the recipe source is the robust choice; that part of the scraper will keep working.
- `validate_modification_safety` and `apply_modifications_batch` are the right ideas already
  written. They are wired to nothing, not wrong.

---

## 5. Open questions

Flagged rather than guessed.

1. **Live extraction quality is unmeasured.** No API key is available in this environment, so no
   run of Step 1 was executed. Everything about extraction behaviour is read from code, prompts,
   and the committed outputs. The multi-modification failure in A2 is evidenced by an artifact
   from an older code version, not from a run I performed.
2. **Whether AllRecipes exposes a vote count at all.** The Featured Tweaks surface implies a
   ranking exists. Whether it is in the DOM, in an internal API, or only implied by ordering is
   unconfirmed. This determines whether A1 is a scraper fix or a product redefinition, and it is
   the first thing worth checking.
3. **How `find` behaves in practice.** A4 assumes the model emits short substrings because the
   prompt teaches that. If it emits whole instruction lines, matching improves and replacement
   semantics change. One live run would settle it.
4. **Which code produced `data/enhanced/`.** It is not in git history under any commit I searched.
   Whether it was uncommitted local work or hand-edited is unknown, and it matters for how much of
   the demo can be trusted.
5. **Star ratings may be systematically wrong.** `scraper_v2.py:51` falls back to counting
   `svg.icon-star` elements, which likely counts all five stars rather than filled ones. The
   observed distribution includes 3 and 4, so the `aria-label` path worked at least sometimes.
   Not verified per-review.
6. **Scraping posture.** No robots.txt or terms review was performed. Worth resolving before any
   scale-up.

---

## 6. Where I would spend the time

Cheapest first, by ratio of trust gained to effort.

1. **Make failure loud.** Wire up `validate_modification_safety`, and refuse to emit a
   `ChangeRecord` when the text did not actually change. Fixes A3 and A7, and turns every other
   bug from silent to visible. This is the single highest-value change in the repo.
2. **Build a fixture-based offline test set.** Hand-label the ~30 discrete modifications in the
   existing corpus, then measure extraction against them. Nothing else can be judged until there
   is a denominator. This is the missing piece for "does it parse ALL modifications".
3. **Change the unit from review to modification.** One review yields N modification objects, each
   with its own type, rationale, and edits. Requires the schema change in A2 plus wiring
   `apply_modifications_batch`, and unblocks the per-line explanation the product needs.
4. **Replace whole-string fuzzy matching with token or quantity-aware anchoring**, and match
   within a line rather than across the list. Fixes A4 and makes `technique_change` viable.
5. **Decide what "highest voted" means with the data that actually exists.** Either recover the
   vote signal, or state plainly that ranking is by rating and recency, and stop claiming
   otherwise.
6. **Replace the regex gate with the LLM, or delete it.** A5 shows a regex is deciding what the
   model may read. Sending all reviews and letting extraction return an empty result is both
   simpler and more accurate.
