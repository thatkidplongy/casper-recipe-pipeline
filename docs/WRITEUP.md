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
4. **Fixed the three highest-value defects**, test-first, once the diagnosis said
   which ones mattered. See *What I fixed* below.

Diagnosis came before any fix on purpose. With this many interacting defects,
knowing which ones matter is the whole job; two of them turned out to be a few
lines each.

---

## Assumptions

Stated up front, because several of them shape every number in this document and
a reader should be able to reject one and know what it costs.

- **The featured-tweak list order is the ranking.** The product premise is
  "highest voted", and no vote count exists anywhere in the scraped data. The
  order AllRecipes returns featured tweaks in is the only ranking signal
  captured, so it is used verbatim and labelled as what it is. If real vote data
  exists on the site, Finding 3 becomes a scraper fix rather than a product
  question.
- **The 12 featured tweaks are the corpus.** They turned out to be
  byte-identical to the 12 reviews flagged `has_modification`, so scoping to them
  loses nothing and matches the brief's framing.
- **My hand labels are ground truth.** All 28 expected modifications and 9
  exclusions in `golden_tweaks.json` are my reading of the reviews, reviewed and
  approved before anything was built on them. A different labeller would draw
  some lines differently, particularly on `10813-t4`, where "a whole cup of white
  sugar" is the recipe's existing amount and I treat it as no modification.
- **A change made without a stated amount is not a miss.** Eight of the 28 are
  marked `underspecified`. A model that declines to invent a quantity the
  reviewer never gave is behaving correctly, so these are reported separately and
  never counted against recall.
- **Diagnosis stubs the model; measurement does not.** Pipeline defects were
  found with the single LLM call replaced by canned extractions, so deterministic
  bugs could be separated from model variance. Every stubbed figure is a floor.
  The 85% recall is a live measurement with nothing stubbed.
- **The scraper and any UI are out of scope**, by instruction. Findings there are
  recorded as future work, not fixed.
- **Model and provider are a configuration choice, not a finding.** The measured
  numbers are `openai/gpt-oss-20b` through Groq, chosen because it was available
  within a free-tier budget. A larger model would likely score higher; the point
  of the harness is that re-measuring is one command.
- **Three runs, not ten.** The standard this repository sets asks for ten. The
  provider's daily token cap makes ten impossible in a day. The figures are
  provisional and say so.

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

### The same defect, in the tool I built to measure it

The strongest illustration in this document is not in the pipeline. It is in my
own evaluation harness, and I did not put it there deliberately.

The first live run of the golden set reported this:

```
run  1: recall 0%  (0/20)  spurious 0  zero-tweaks correct 2/2
```

Every single call had failed. The harness scored it anyway, and **credited itself
2 out of 2 on the two fixtures that expect no modifications** — because a total
outage returns nothing, and nothing is exactly what those two fixtures expect. A
complete failure was indistinguishable from partial success, and the one number
that looked healthy was the one measuring fabrication.

Two causes, both instances of the pattern this document is about.

1. **`extract_modifications` returned `[]` for two different things**: the model
   correctly finding no modification, and every request erroring. The caller had
   no way to tell them apart, so the harness treated an outage as a set of
   correct empty answers. This is the identical conflation the pipeline makes when
   it publishes an unchanged recipe as enhanced.
2. **The harness called `extract_modification`, which no longer exists.** I had
   renamed it to `extract_modifications` two commits earlier, updated the test
   stubs, and missed the harness. Attribute lookup on a live object fails only at
   call time, so nothing broke at import. The `except Exception` around the call
   swallowed the `AttributeError` and recorded an empty answer. I had already
   written this exact lesson into `docs/ERRORS.md` and then failed to apply it to
   my own tool one commit later.

Both are fixed. `extract_modifications` now raises `ExtractionError` when no
valid extraction could be obtained, and returns `[]` only when the model genuinely
extracted nothing. Permanent API errors, a 404 for a missing model or a 401 for a
bad key, are no longer retried at all. A run in which every extraction fails now
aborts, prints the underlying cause, reports no score and exits non-zero:

```
run 1: ABORTED. all 12 extractions failed; first was 10813-t1:
       ExtractionError: no valid extraction after 3 attempt(s)

No score is reported. Every extraction failed, so there is
nothing to measure. Fix the cause and rerun.
```

A partial failure is reported as a degraded run rather than a clean measurement,
and the failed fixtures are named. Zero-modification fixtures are never credited
when the call errored.

**Why this belongs in the writeup rather than being quietly fixed.** The
temptation with a bug in your own tooling is to fix it and say nothing. But it is
evidence, and it is better evidence than anything I found by reading the
pipeline. It shows the failure mode is not a junior engineer's oversight in one
codebase. It is what happens by default whenever a system reports a result
without distinguishing "this is the answer" from "I never got an answer". I
diagnosed exactly that defect, wrote it up as the headline finding, built a tool
to measure it, and reproduced it in the tool. A measurement instrument that
cannot fail loudly is worth less than no instrument, because it produces numbers
you will believe.

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

> **The schema ceiling is removed, and extraction was measured live at 85%
> recall over 3 runs.** Details in *What I fixed* and *The live baseline*. The
> diagnosis below describes the behaviour as found.

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

### The live baseline

Measured against `openai/gpt-oss-20b` through Groq. Full artifact, including
every raw model response, in `docs/evidence/golden_set_run_20260902T024448Z.md`.

| | |
| --- | --- |
| Runs completed | **3 of 5** |
| Recall on exact modifications | **85%** (51/60) |
| Failed extractions | **0** |
| Spurious edits | 19 |
| Zero-modification fixtures correct | 3 of 6 |

Three runs, not the ten the standard asks for. Groq's free tier allows 200,000
tokens per day and one run costs about 33,600, so ten runs is not possible in a
day. The run aborted at run 4 on the daily cap, kept the three completed runs,
and the log says so at the top. **Treat these as provisional**: the harness runs
ten the moment there is budget, and the number is one command away.

**Recall degrades with the number of modifications in a review**, which is the
central claim of Finding 2, now measured:

| Fixture | Modifications | Recall | Spurious |
| --- | --- | --- | --- |
| `10813-t3` | 3 | 100% | 0 |
| `77935-t3` | 2 | 100% | 0 |
| `77935-t4` | 2 | 100% | 0 |
| `10813-t1` | 2 | 83% | 1 |
| `10813-t4` | 3 | 67% | 3 |
| `10813-t2` | **5** | **67%** | 4 |

The hardest fixture is the worst, and it misses the same two every time: the
cream of tartar and the refrigeration step, both of which belong in the
instructions rather than the ingredients.

**The fabrication finding reproduced, and it is not intermittent.** Fixture
`77935-t2` is the review that reads *"Very good as is but when I make it again I
will use fresh ginger"*. The model substituted fresh ginger for ground in
**3 of 3 runs**, every time, anchored at `1.5 teaspoons ground ginger`. That is
the defect already shipped in `data/enhanced/`, reproduced live and
deterministic.

The control holds: `19117-t2`, *"I would prefer some more apple chunks... that is
just my preference"*, correctly returned nothing in **3 of 3 runs**. So the model
can decline. It fails specifically on future intent phrased as a plan, and
handles a stated preference correctly. That contrast is what makes it a finding
rather than an anecdote, and it is why both zero-modification fixtures are in the
golden set.

**A caveat on the 19 spurious edits.** Many are the same modification anchored to
a different line rather than an invention. `19117-t1` contributes five, all
placement disagreements. The `77935-t2` ones are genuine fabrications. The log's
*What was missed and what was invented* section distinguishes them by naming the
intent, which is why that section exists. The scorer counts a placement
disagreement twice, once as a miss and once as a spurious edit, so both recall
and the spurious count are pessimistic. Fixing that scoring is the next change
worth making, and it is deliberately not done here so the number is not tuned
after seeing it.

### The attempt that failed first, and the sixth defect it found

Reaching that measurement took three failed attempts, and the first was the most
instructive. Every call returned `429 insufficient_quota`, a permanent billing
state rather than a transient error.

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

### `23394a0` Every discrete modification survives

The extractor returns a list. One review yields one `ModificationObject` per
discrete modification, each with its own category, its own reasoning and its own
edits, and each is attributed separately in the output under the same
`source_tweak_id`.

Validated through the whole pipeline on fixture `10813-t2`, the worst case in the
corpus:

| | Before | After |
| --- | --- | --- |
| Modifications recorded from that review | 1 | **5** |
| Categories surviving | 1 | **4** |
| Distinct rationales | 1 | **5** |
| Instruction-level change reaching the recipe | no | yes |

```
10813-t2  quantity_adjustment   Halving the white sugar shifts the ratio toward brown
10813-t2  quantity_adjustment   More brown sugar gives a chewier, more flavourful cookie
10813-t2  removal               Omitting the water reduces spread
10813-t2  addition              Cream of tartar helps the cookies hold their shape
10813-t2  technique_change      Chilling the dough stops it spreading when baked
```

The prompt now states the rule the brief hints at, in the brief's own terms: "I
added an egg and halved the sugar" is two modifications, not one. It also carries
the exclusion rules the golden set encodes, so preferences, future intentions and
advice to others are not extracted, and it instructs the model not to invent an
amount the reviewer never gave.

The response parser accepts a bare single object as well as the list, because
earlier prompts produced that shape and a model will occasionally still answer
that way. Treating it as a one-element list is cheaper than a retry and loses
nothing.

> **Measured, not assumed.** `gpt-oss-20b` returned all five discrete
> modifications for `10813-t2` with five separate rationales, spanning all four
> categories. Recall on that fixture is 67% because two of the five were anchored
> to a different line than the fixture expected, not because they were missed.
> Across the whole set, recall is 85% over 3 runs with zero failed extractions.
> The full numbers and every raw response are in *The live baseline* below and in
> `docs/evidence/`. Three runs rather than ten because the provider's daily token
> cap makes ten impossible; the figures are provisional and the harness runs ten
> the moment there is budget.

### What these fixes do not touch

- **Vote data** still does not exist. Rank order is now the featured-tweak list
  order, which is honest and explicit but is not a vote count. Finding 3's
  product question is unresolved.
- ~~**API error handling.**~~ Fixed. Permanent errors are not retried, rate
  limits wait as long as the API asks, a failed extraction raises rather than
  returning empty, and generation failures retry. Zero failed extractions in the
  measured run, against 11 of 36 before.
- **Instruction-level matching.** `technique_change` remains unreachable.

---

## Future improvements, in order

1. ~~Never emit a change record for text that did not change; never publish with
   zero applied edits.~~ **Done, `46512a7`.** What remains under this heading:
   catch typed API errors and stop on the fatal ones instead of retrying a
   billing failure for an hour.
2. ~~Change the unit from review to modification.~~ **Done and measured**, 85%
   recall over 3 runs. What remains: fix the scorer, which counts a placement
   disagreement twice, and run ten repetitions once there is token budget.
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
`docs/DECISIONS.md`, as What / Why / Rejected. **Challenges overcome**, meaning
any approach that cost more than two attempts and what finally worked, are in
`docs/ERRORS.md`; that file is the honest record of this project's dead ends,
including two defects I introduced in the tooling built to measure the defects.
Reproduction scripts and the raw measurement artifacts for every claim above are
in `docs/evidence/`.
