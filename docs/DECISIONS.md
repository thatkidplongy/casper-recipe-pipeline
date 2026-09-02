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
