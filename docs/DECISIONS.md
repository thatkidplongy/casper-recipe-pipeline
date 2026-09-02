# Decisions

Every significant decision on this project, newest first.

Each entry uses this shape:

## <Decision>

**What** — the decision, in one line.

**Why** — the reasoning that made it the right call.

**Rejected** — the alternatives considered and why each lost. An entry without
this section is not a record of a decision.

---

<!-- Entries go below this line. -->

## Transcript exporter depends only on the standard library

**What** — `scripts/export_transcript.py` and its tests use nothing outside the
Python standard library, and the tests run on `unittest` rather than pytest.

**Why** — the exporter has to run when the pipeline's own dependencies are not
installed, which is the normal state of a fresh container. It also has to run on
whatever Python is present; the container ships 3.11 while the project requires
3.13. A tool for capturing a session must not fail because the session had not
finished setting itself up.

**Rejected** — *pytest*: better ergonomics, but adding a dev dependency to run
seven files of tests is an infrastructure change nobody asked for, and it would
have to be installed before the exporter could be trusted. *A shell script*:
no dependency at all, but redaction over nested JSON is not something to write
in `sed`, and it would have been untestable.

## Transcript is located by session id, with a newest-file fallback

**What** — the exporter derives the Claude Code project directory from the
working directory, then prefers the file named by `CLAUDE_CODE_SESSION_ID`,
falling back to the most recently modified `.jsonl` in that directory.

**Why** — the session id is exact when present and makes the export
reproducible. The fallback keeps the script usable outside a live session, for
example when re-exporting an old transcript. `--transcript` overrides both.

**Rejected** — *hardcoding the path*: breaks for every other user and machine.
*Newest file only*: silently exports the wrong session when two are open, which
is the failure mode most likely to go unnoticed.

## Selection must become deterministic and ranked before any launch

**What** — random review selection is treated as a launch blocker, not a
refinement. No enhanced recipe ships to a user until selection is deterministic,
ranked, and recorded in the output.

**Why** — `extract_single_modification` calls `random.choice` with no seed
(`tweak_extractor.py:136`). On the chocolate chip cookie recipe, four reviews
carry `has_modification`, so each is chosen roughly a quarter of the time. Three
consecutive real runs produced three different published recipes from the same
input.

One of those four is a **3-star review** from a reviewer who found the result
bland and never fully satisfied: *"still not quite as flavorful ... the flavor
falls a bit flat for me."* It is selected about 25% of the time. So a platform
whose entire premise is applying the *highest voted community-tested*
modification publishes the least satisfied reviewer's version of the recipe on
roughly one run in four, presents it as a community improvement, and records
nothing about why that review was chosen.

Nothing in the output identifies which of the four was used or what the
alternatives were, so the result is neither reproducible nor explainable. The
line-level diff the product promises cannot be trusted while the line it
explains changes between runs.

**Rejected** — *Seed the RNG*: makes runs reproducible but still picks
arbitrarily, and a fixed seed disguises the problem rather than fixing it.
*Take the highest rated review*: ratings cluster hard at 5, so it is close to
arbitrary too, and rating measures the original recipe rather than the tweak.
*Ship it and rank later*: the citation shown to the user is the product, so
publishing an unexplainable one spends the trust the feature depends on.

## Diagnosis runs stub the extraction step

**What** — for diagnosing pipeline defects, the single OpenAI HTTP call is
replaced with canned extractions written against the actual review text and the
actual ingredient lines. Selection, response parsing, Pydantic validation, edit
application, attribution and file writing all run as production code. The stub
is diagnosis only; the phase 2 baseline runs against the real model.

**Why** — it separates two failure sources that otherwise blur together. Given a
reasonable extraction, does the pipeline handle it correctly? That question has
a definite answer, and the answer turned out to be no in three distinct ways: a
change record emitted for a replace that altered nothing, an instruction edit
dropped silently below the similarity threshold, and a published recipe
identical to the original still labelled "Community Enhanced" with a citation
and a stated impact. Every one of those is deterministic and fixable with
certainty. Mixed with model variance they would read as flakiness and get
explained away.

**The live failure rate will be higher than these numbers, not lower.** The stub
holds extraction quality constant at "competent". A real model adds its own
variance on top: paraphrased `find` strings that miss the anchor, dropped
modifications from multi-tweak reviews, and off-enum values that fail
validation and discard the review entirely. Both paraphrased finds that caused
failures in these runs are exactly what a real model emits, and the sample
outputs already committed to the repository show it happening. Treat the stubbed
figures as a floor.

**Rejected** — *Run live for diagnosis*: the honest-looking option, but random
review selection plus temperature means two runs differ for reasons that have
nothing to do with the defect under investigation, so a deterministic bug looks
like a flake and survives. *Unit-test the modifier in isolation*: catches the
change-record bug but not the silent zero-change publish, which only appears
when Step 2 and Step 3 run together. *Wait for an API key*: blocks all
diagnosis on an environment problem, and the deterministic defects do not need
a model to demonstrate.

## Featured-tweak list order is the ranking, and every tweak is applied

**What** — selection reads the scraped `featured_tweaks` list and applies every
entry in list order, rank 1 first. Each applied modification records its
`source_tweak_id` and `source_tweak_rank` in the output. Recipes with no
featured tweaks fall back to flagged reviews in file order.

**Why** — the brief asks for the highest voted tweak and the data has no vote
count, so any ranking claim would be a fiction. AllRecipes' own featured-tweak
ordering is the only ranking signal the scrape captured, and it was being thrown
away in favour of `random.choice` over all flagged reviews. Using it verbatim is
defensible, reproducible, and honest about what it is. Applying all of them
rather than one removes the choice that was causing the damage: a quarter of runs
were publishing the 3-star reviewer's version with nothing recording why.

Recording the tweak id matters as much as the ordering. It turns rank into a
product decision that can be argued about and changed, rather than an accident
invisible in the output.

**Rejected** — *Seed the RNG*: reproducible but still arbitrary, and a fixed seed
disguises the problem. *Rank by star rating*: ratings cluster at 5, and a rating
measures the original recipe rather than the tweak. *Apply only rank 1*:
deterministic, but discards eleven of twelve community tweaks and keeps the
product dependent on a ranking signal that does not exist. *Wait for real vote
data*: blocks a correctness fix on a scraper change that is out of scope.

## The extraction client is provider-agnostic

**What** — `TweakExtractor` reads its model, endpoint and key from configuration
rather than hardcoding them: `LLM_MODEL`, `LLM_BASE_URL`, and `LLM_API_KEY`
falling back to `OPENAI_API_KEY`. Resolution order is explicit argument, then
environment, then default. The default model id is `gpt-4o-mini`, and a test
asserts that string appears in README.md so code and docs cannot drift apart
again.

**Why** — there are no OpenAI credits on this account, and a reviewer should be
able to run the evaluation without a funded OpenAI account of their own. Nothing
in this pipeline needs OpenAI specifically: Groq, Together, Fireworks,
OpenRouter and a local server all speak the same API, so the endpoint is
configuration, not a constant.

It also closes a defect rather than working around one. The audit recorded that
the code hardcoded `gpt-3.5-turbo` while the README documented GPT-4o-mini, so
the documented model was not the one that ran. Making the model configurable
with a documented, test-enforced default resolves that inconsistency instead of
leaving it and adding a flag beside it.

**Rejected** — *Skip the measurement*: the golden set exists precisely so
extraction quality is a number rather than an opinion, and leaving it unmeasured
because of a billing state would waste the work and contradict the standard the
repository is built on. *Hardcode a second provider*: swapping one hardcoded
endpoint for two is the same defect with more branches, and it would need
editing again for the third. *Wait for credits*: blocks a reviewer on the
author's billing, which is not a dependency an evaluation should have.
