# Recipe Enhancement Pipeline: does it actually work?

**Short answer: no, and the reason is not that it needs tuning.**

The pipeline runs. On a good day it produces a file that looks exactly like the
product brief describes: an enhanced recipe, a community citation, a line-level
record of what changed and why. That appearance is the problem. The output is
untrustworthy in both directions, the data schema cannot represent half the
corpus, and the "highest voted" premise the product rests on has no
corresponding field anywhere in the scraped data.

None of that is fixable by improving the prompt.

---

## What I did

The guideline was four hours. **I went past it to land the two fixes.** The
diagnosis, the golden set and the harness fit inside the window; the fixes did
not, and stopping with a diagnosis and no repaired code seemed the worse
outcome. Recording it here rather than leaving it to be noticed in the commit
timestamps.

1. **Read the pipeline end to end** and mapped every assumption the code makes
   that the brief does not support. Findings in `docs/pipeline-audit.md`.
2. **Ran it.** Built the environment, ran the pipeline across all six recipes,
   eight times, to characterise failures under a selection step that is random.
3. **Built a measurement instrument.** Hand-labelled the 12 AllRecipes featured
   tweaks into a golden set of 28 discrete modifications, and wrote a harness
   that scores extraction against it over repeated runs.
4. **Fixed the two highest-value defects**, test-first, once the diagnosis said
   which ones mattered. See *What I fixed* below.

Diagnosis came before any fix on purpose. With this many interacting defects,
knowing which ones matter is the whole job; two of them turned out to be a few
lines each.

---

## Finding 1: the system cannot tell "nothing to do" from "something is broken", and it always reports the first

> **Faces 1 and 2 are fixed.** Face 3, the API error handling, is not. Details in
> *What I fixed*. The diagnosis below describes the behaviour as found.

This is the whole story. Everything else in this document is downstream of it.

There is no field anywhere in the output, and no distinct return value anywhere
in the code, separating a modification that was **applied** from one that was
**attempted and missed**, from one that was **invented**, from a run that
**failed outright**. All four end the same way: a plausible-looking file, or a
quietly smaller number in the summary report.

The single missing distinction shows three faces:

1. **It applies a change nobody suggested.** A review saying "Very good as is"
   produces a ginger substitution in the shipped output.
2. **It claims changes it never made.** An unchanged recipe is published as
   "Community Enhanced" with a citation and a stated impact, in half of all runs.
3. **It reports a billing outage as an empty corpus.** An API account with no
   credits presents as every recipe simply having no community enhancements.
   Detail in the measurement section below.

The first two are set out here. The third arrived by accident while trying to
measure the model, and is the clearest proof that the problem is structural
rather than a modelling shortfall: no prompt change affects it at all.

### It invents changes nobody made

One review in the corpus reads, in full:

> It was amazing!! The portion is only enough for 2-3 people. **Very good as is**
> but **when I make it again I will use fresh ginger** to give it more of a
> ginger flavour.

The reviewer changed nothing and describes an intention for next time. The
committed output in `data/enhanced/` applies it anyway: it swaps ground ginger
for fresh, records the edit, cites this reviewer, and states the benefit as
fact. The platform manufactured a community-tested modification from a review
whose point was that no modification was needed.

### It claims changes it never made

The mirror image. Across eight full runs, Spicy Apple Cake was published in four
of them with ingredients and instructions **byte-identical to the original**,
titled "Spicy Apple Cake (Community Enhanced)", carrying `total_changes: 0`, an
empty `changes_made`, and `expected_impact: "More apple chunks."` The citation
names a reviewer who wrote that more apple is *"just my preference"* and made no
change at all.

The cause is mechanical. A paraphrased `find` of "2 cups apple" scores 0.53
against `2 cups apple - peeled, cored, and chopped`, below the 0.6 similarity
threshold, so the only edit is dropped. Step 3 then builds and saves the
attribution record regardless of whether Step 2 changed anything.

A third variant sits between them: the modifier fuzzy-matches a line, then
performs an **exact substring** replace on it. When the substring is absent the
text is unchanged but a `ChangeRecord` is still emitted with `from_text` equal to
`to_text`. Across eight runs the cookie outputs claimed 25 changes when 23
altered the recipe.

### Why this is one finding, not three

Nothing in the output distinguishes **applied**, **attempted and missed**, and
**invented**. A user reading the diff cannot tell them apart, and neither can
the summary report, which counts changes that did not happen.

The product is the citation. A confident, well-formatted explanation of
something that did not happen is worse than no feature, because it spends trust
that the real feature would need.

**The fix is small.** Refuse to emit a change record when the text did not
change. Refuse to publish when zero edits applied. Both are a few lines, in
`recipe_modifier.py` and `pipeline.py`. There is already a
`validate_modification_safety` method written for roughly this purpose, and
nothing calls it. It would not have caught the case above either: run against
the near-miss it returns `is_safe=True` with only a warning.

---

## Finding 2: half the corpus cannot be represented, at any model quality

The brief's own cue is right. *"I added an egg and halved the sugar"* is two
discrete modifications, and reviews like that are the norm rather than the edge
case.

`ModificationObject` carries **one** `modification_type` and **one** `reasoning`
for a flat list of edits. So a review containing an addition, a removal, a
quantity change and a technique change gets one label and one explanation.

From the hand-labelled set:

| | |
| --- | --- |
| Featured tweaks | 12 |
| Discrete modifications in them | 28 |
| Tweaks containing more than one modification | 9 |
| Tweaks spanning more than one modification *type* | 6 |
| Largest single review | 5 modifications across 4 types |

Six of twelve are unrepresentable by the current schema no matter how good the
model is. That is a structural ceiling, not an accuracy problem, and no prompt
change moves it.

The evidence is already in the repository. The committed cookie output was
generated from a review listing four numbered tweaks. It captured the sugar
change and dropped the omitted water, the cream of tartar and the refrigeration
step, with no record that anything was missed.

**The fix is a schema change**, not a prompt change: one review yields *N*
modification objects, each with its own type, rationale and edits. The code to
apply them already exists, unused, in `apply_modifications_batch`.

---

## Finding 3: "highest voted" does not exist in the data

> **The random selection is fixed. The missing vote data is not, and cannot be
> from inside this repository.** Details in *What I fixed*.

The product premise is applying the *highest voted community-tested*
modification. There is no vote, helpful, or upvote count on any review in
`data/`. The scraper documents dropping it, in a comment:

> `# Take the tweaks as-is without sorting by helpful count`

What actually happens is `random.choice` with no seed. Three consecutive real
runs on the same recipe produced three different published recipes.

**Roughly one run in four publishes the 3-star reviewer's version.** That
reviewer applied their changes and concluded *"the flavor falls a bit flat for
me"*. A platform built on surfacing the best community modification publishes
the least satisfied reviewer's attempt a quarter of the time, presents it as an
improvement, and records nothing about why that review won.

The output has no seed, no run id, no candidate list. It is neither reproducible
nor explainable. A line-level diff that changes between runs cannot be the
feature.

One more thing, which is almost funny: the scraper *does* capture a
`featured_tweaks` field, populated for four of six recipes. The pipeline never
reads it. The one Featured Tweak signal that was successfully scraped is
discarded in favour of the generic review list.

---

## Finding 4: it does not survive contact with five recipes, let alone scale

Running all six, eight times:

| Recipe | Produced a file | Zero real changes | False change records |
| --- | --- | --- | --- |
| Best Chocolate Chip Cookies | 8/8 | 0 | 2 |
| Creamy Sweet Potato Soup | 8/8 | 0 | 0 |
| Nikujaga | 8/8 | 0 | 0 |
| Spicy Apple Cake | 8/8 | **4** | 0 |
| Mango Teriyaki Marinade | **0/8** | never runs | n/a |
| Spiced Purple Plum Jam | **0/8** | never runs | n/a |

Two recipes never produce output because zero reviews were scraped for them.
That is correct behaviour on broken data, and the data is broken: the marinade
shows 12 ratings on the site. The scrape is a plain `requests.get` with no
JavaScript execution, so it captures only server-rendered markup. The cookie
recipe yielded 9 reviews against 19,353 ratings.

Selecting the *highest voted* tweak from 9 of 19,353 reviews is not a sampling
problem. It is a different product.

Beyond the data, the runtime shape is one serial API call per recipe with no
concurrency, no caching, no backoff, and no cost tracking. Three retries fire
the identical prompt at temperature 0.1, so a parse failure retries into the
same failure and a rate limit burns all three attempts instantly.

---

## Finding 5: the review gate is a regex, and it decides what the model may read

`has_modification` is set by five regular expressions in the scraper. Two of them
match intent rather than action:

- `(next time|will make again|definitely make)`
- `(more|less|extra) ([\w\s]+)`

That is why the "Very good as is" review reached the model at all. It is also
why *"I would prefer some more apple chunks... that is just my preference"* is
flagged as a modification.

It misses in the other direction too. The pattern `I (added|used|...)` requires
the pronoun immediately before the verb, so both of these are dropped before the
model ever sees them:

- *"Also, I threw in carrots since i had them around"*
- *"I forgot to get ground ginger and used minced"*

The gate is enforced twice, and a recipe with no flagged review is abandoned
entirely. A scraper-side regex is deciding the product's input.

---

## Measurement

Reading the code proves selection is random. It cannot tell you how well the
model extracts. So I built the instrument.

`src/llm_pipeline/fixtures/golden_tweaks.json` holds the 12 featured tweaks
hand-labelled into 28 discrete modifications, each with a type, a target, a
one-line intent, the recipe line it anchors to, and a specificity flag. Nine
exclusions record preferences, future intentions, advice to others and
speculation that reviewers did not act on.

Two labelling rules matter:

- **Underspecified expectations are reported separately.** Eight of 28 changes
  were made without a stated amount. A model that declines to invent a quantity
  is behaving correctly and is not scored as a miss.
- **Two fixtures expect nothing at all.** They describe no change. The extractor
  is correct only if it returns empty. These are the fabrication detectors, and
  one of them is the exact review the shipped pipeline invented a change from.

`scripts/run_golden_set.py` runs the set N times and reports recall on exact
expectations, spurious edits, and zero-tweak correctness. A `--stub` control
returns the expectations verbatim and scores 100%, so a live number can be read
against a known ceiling rather than in isolation.

### The live baseline: blocked, and the attempt found a sixth defect

The measurement did not happen. The API key has no credits, and every call
returned `429 insufficient_quota`, a permanent billing state rather than a
transient error. **There is no live pass rate in this document.** Every figure
here comes from stubbed runs and is a floor.

The failed attempt was not wasted, because how the pipeline handled it is a
finding on its own, and a more serious one than anything the pass rate would
have shown.

**It retries a permanent error, blindly, forever.** `extract_modification`
catches bare `Exception` and retries three times with no backoff and no
inspection of the error. The OpenAI SDK retries twice more internally by
default. So a single review costs up to nine HTTP requests against an account
that cannot possibly succeed. Measured from the run:

| | |
| --- | --- |
| Attempt 1 on the first fixture | 44.2s |
| Attempt 2 | 2.6s |
| Attempt 3 | 22.8s |
| One fixture, total | 69.6s |
| Projected for the full 12 × 10 run | **about 68 minutes** |

An hour of wall time, and up to 1,080 requests, to learn something the first
response stated unambiguously.

**And the failure is invisible downstream.** The bare `except` returns `None`,
which `process_single_recipe` cannot distinguish from "this recipe had no
reviews with modifications". In production, an expired card would present as
every recipe quietly having no community enhancements. No alert, no error
surface, no distinction between a billing outage and an empty corpus. The
summary report would show a smaller number and nothing else.

This is the same defect as Finding 1 wearing different clothes: **the system
cannot tell the difference between "nothing to do" and "something is broken",
and it always reports the first.**

The fix is standard: catch the typed SDK exceptions, treat `insufficient_quota`
and `invalid_api_key` as fatal and stop immediately, retry only `RateLimitError`
and transient server errors, with exponential backoff, and propagate a distinct
failure state so the caller can tell a broken run from an empty one.

### To produce the live number

Add credits to the account, then from the repo root on this branch:

```bash
uv sync
uv run python scripts/run_golden_set.py --runs 10
```

The harness is written, tested, and validated against a control. It reports
recall on the 20 exact expectations, spurious edits, and whether the extractor
correctly returns nothing on the two fixtures that describe no change. Watch
`77935-t2`: if the live model invents a ginger substitution there, the defect
already shipped in `data/enhanced/` is reproduced with a count against it.



---

## What I fixed

Two changes, both test-first, both validatable offline with no API key.

### `46512a7` Never report a change that did not happen

`RecipeModifier.apply_edit` now records a replace only when the text actually
differs, and logs a warning naming the similarity score when it does not.
`process_single_recipe` refuses to publish when no edit applied, rather than
producing an unchanged recipe titled "(Community Enhanced)".

Measured over the same eight-run sweep:

| | Before | After this fix |
| --- | --- | --- |
| Apple cake zero-change publishes | 4 of 8 | 0 |
| Cookie false change records | 2 | 0 |
| Changes claimed vs actually made | 66 / 64 | 54 / 54 |
| **Recipes published per run** | **4.00** | **3.38** |

**Published output dropped, and that is the fix working.** Unchanged recipes now
fail loudly instead of shipping. Apple cake went from publishing every run to
publishing half of them, and the soup lost one run in eight to the same guard.
A platform that ships fewer recipes but tells the truth about all of them is
strictly better than one that fills the gap with fabrications.

### `7c579d8` Deterministic, complete, attributable selection

`random.choice` is gone. The pipeline reads the scraped `featured_tweaks` list,
the ranking signal that existed in the data and was never read, and applies
every entry in that order. Each modification records `source_tweak_id` and
`source_tweak_rank`, so the output says which tweak produced which change.

| | Before | After |
| --- | --- | --- |
| Tweaks applied per run | 1 of 4, at random | 4 of 4, always |
| Changes per run, cookies | 1 to 4 | 9 |
| Three runs byte-identical | no | yes, `created_at` aside |
| 3-star tweak included | 25% of runs | every run, and named |

**A third source of nondeterminism surfaced only at the end**, and it is the
most instructive thing in this section. After the selection fix, all ten unit
tests passed and three real runs still produced different files.
`enhancement_summary.change_types` was built with `list(set(...))`, and Python
randomises string hashing per process, so set iteration order varies between
runs. The unit tests compared ingredients and tweak ids, not the summary, so
they were green over a live defect. Only whole-artifact comparison across
separate processes caught it.

The lesson generalises: **a determinism test that checks selected fields is not
a determinism test.**

### The two fixes together

Applying every tweak instead of one recovers the output that the first fix gave
up, without weakening it:

| | Original | After `46512a7` | After both |
| --- | --- | --- | --- |
| Recipes published per run | 4.00 | 3.38 | 4.00 |
| Changes actually applied per run | 8.0 | 6.8 | **18.0** |
| False change records | 2 | 0 | 0 |
| Runs byte-identical | no | no | yes |

The recovery is legitimate rather than a relaxed guard. The guard still fires,
it just no longer discards a whole recipe when one tweak misses. Apple cake now
publishes with its rank 1 tweak applied while rank 2 is dropped and logged:

```
WARNING  Tweak 19117-t2 changed nothing (1 edits, none matched); not recorded
```

That is the shape the product needed all along. More community tweaks reach the
recipe, every one of them is real, and each is attributable to its source.

### What these fixes do not touch

- The **schema ceiling** in Finding 2, which is deliberately deferred. One review
  still yields one `modification_type` and one `reasoning`, so six of twelve
  tweaks remain unrepresentable.

  The reason for deferring it is a process rule rather than a scheduling
  accident. Splitting one review into several modification objects requires
  changing the extraction prompt, and a prompt change is a behaviour change: it
  has to be rerun against the golden set ten times and reported as a pass rate,
  never as "it worked". **The account has no API credits, so that rerun cannot
  happen.** Shipping a prompt change I could not measure would contradict the
  standard this repository is built on, and it would be exactly the kind of
  unverified confidence this document criticises everywhere else.

  The golden set and the harness are built and tested, so the measurement is one
  funded run away. That is the right place to stop.
- **Vote data** still does not exist. Rank order is now the featured-tweak list
  order, which is honest and explicit but is not a vote count. Finding 3's
  product question is unresolved.
- **API error handling.** A permanent billing failure is still retried up to
  nine times and still returns `None`, indistinguishable from an empty corpus.
- **Instruction-level matching.** `technique_change` remains unreachable.

---

## What I would do next, in order

1. ~~Never emit a change record for text that did not change; never publish with
   zero applied edits.~~ **Done, `46512a7`.** What remains under this heading:
   catch typed API errors and stop on the fatal ones instead of retrying a
   billing failure for an hour.
2. **Change the unit from review to modification.** *N* modification objects per
   review, each with its own type and rationale. Unblocks the per-line
   explanation the product promises.
3. ~~Make selection deterministic and explainable.~~ **Done, `7c579d8`.** Every
   featured tweak is applied in rank order and attributed by id. What remains is
   the product question: decide honestly what "highest voted" can mean given
   data that has no votes.
4. **Replace whole-string fuzzy matching** with anchoring that understands
   quantities and units, and match within a line rather than across a list.
   Currently every instruction-level edit is unreachable, which kills
   `technique_change` entirely.
5. **Fix the input gate.** Send reviews to the model and let extraction return
   empty, rather than letting five regexes decide what the model is allowed to
   see.
6. **Then, and only then, tune the prompt** against the golden set, reporting a
   pass rate over 10 runs.

Out of scope by instruction and recorded as future work: the scraper, and the UI.

---

## What I could not verify

- **Whether AllRecipes exposes a vote count anywhere.** This decides whether
  Finding 3 is a scraper fix or a product redefinition. It is the first thing
  worth checking.
- **The provenance of the committed sample outputs.** Both files in
  `data/enhanced/` contain a `confidence_score` field and two applied
  modifications. No code in the repository can produce either. They came from a
  version that is not in git history, which is why I treated them as evidence of
  behaviour rather than as a specification.
- **Star ratings may be systematically wrong.** The scraper falls back to
  counting `svg.icon-star` elements, which likely counts all five rather than
  the filled ones.
- **Scraping posture.** No robots.txt or terms review was performed.

## Honest notes on method

Diagnosis runs stubbed the single OpenAI call so that pipeline defects could be
separated from model variance. Everything else ran as production code. The
stubbed figures are a **floor**: a live model adds paraphrased anchors, dropped
modifications and validation failures on top. The `--stub` control in the
harness is the opposite, a **ceiling** by construction.

The reasoning behind that choice, and the alternatives rejected, are in
`docs/DECISIONS.md`. Approaches that cost more than two attempts are in
`docs/ERRORS.md`. Reproduction scripts for every claim above are in
`docs/evidence/`.
