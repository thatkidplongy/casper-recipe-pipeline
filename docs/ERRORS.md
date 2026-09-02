# Errors

Any approach that took more than 2 attempts, and what finally worked. The point
is to stop the next person paying the same cost twice.

Each entry uses this shape:

## <Problem>

**Attempts** — what was tried, in order, and how each failed.

**What worked** — the approach that resolved it.

**Signal for next time** — the cue that would have pointed here sooner.

---

<!-- Entries go below this line. -->

## An empty heading in the rendered transcript appeared unfixable

**Attempts** — (1) Added a guard skipping records that render to nothing, and
regenerated. The heading was still in the output. (2) Rendered the offending
record in isolation; it correctly produced nothing, which contradicted the file
on disk. (3) Suspected a stale file and regenerated again, with the same result.

**What worked** — masking fenced code blocks before searching. The heading was
not being emitted at all. It appeared inside a fenced tool result, because an
earlier debugging step had printed a slice of the Markdown, and that output was
then captured into the transcript on the next export. The detector was matching
the transcript quoting itself.

**Signal for next time** — a transcript export is self-referential. Anything
printed while inspecting it becomes content of the next export. When verifying
generated output that contains prior output, mask code fences first, or the
check will keep finding its own footprints.

## An hour of wall time spent retrying a permanent billing error

**Attempts** — (1) Started the live golden-set run, 12 fixtures over 10
repetitions. The first fixture took 69.6 seconds and produced nothing.
(2) Read the first error as a rate limit and expected it to clear; it did not,
because `429 insufficient_quota` is a billing state, not a transient condition.
(3) Watched the second and third fixtures repeat the same three failed attempts,
and only then worked out where the time was going.

**What worked** — killing the run and reading the retry path.
`extract_modification` catches bare `Exception`, retries three times with no
backoff and no inspection of the error, and the OpenAI SDK retries twice more
internally by default. One review therefore costs up to nine HTTP requests
against an account that cannot succeed. Timings taken from the run itself:
44.2s, 2.6s and 22.8s for the three attempts on the first fixture, projecting to
roughly 68 minutes and up to 1,080 requests for the full set.

**Signal for next time** — an error code carries a category, and the category
decides whether retrying is meaningful. `insufficient_quota` and
`invalid_api_key` are fatal and should stop the run on the first response.
`RateLimitError` and 5xx are transient and deserve backoff. A bare `except
Exception` erases that distinction, and the cost is not just the wasted hour:
the handler returns `None`, which the pipeline cannot tell apart from "this
recipe had no reviews with modifications". A billing outage and an empty corpus
produce the same output. Whenever a handler collapses several failure
categories into one return value, check what the caller can still distinguish.

## A renamed method silently turned two test suites into live API callers

**Attempts** — (1) Renamed `extract_modification` to `extract_modifications` and
changed its return type from a single object to a list, then ran the full suite.
The new suite passed. (2) The two older suites hung. They had stubbed the old
method by assigning to `p.tweak_extractor.extract_modification`, and Python
happily creates a new attribute of that name on the instance. Nothing raised.
The real `extract_modifications` ran instead and started calling the network.
(3) Killed the run after two minutes: 180 outbound connections to
`api.openai.com`, all rejected by the environment proxy.

**What worked** — updating both stubs to patch `extract_modifications` and
return a list. Nothing was spent, because the proxy blocked every request and the
account has no credits, but neither of those is a control I put there.

**Signal for next time** — monkey-patching by attribute name is an unchecked
string reference. A rename cannot break it, so it fails open: the stub silently
stops intercepting and the real implementation runs. The failure mode is
expensive and quiet rather than loud, which is the same missing distinction this
whole repository is about. The system could not tell "the stub is installed" from
"the stub is now a stray attribute and the real client is live".

Cheap defences, in order of preference: patch with
`unittest.mock.patch.object`, which raises `AttributeError` when the target does
not exist; assert the attribute exists before assigning to it; or give the test
suite a client that raises on any outbound call, so a stub that stops
intercepting fails immediately instead of dialling out.

## The evaluation harness scored a total outage as a result

**Attempts** — (1) Ran the golden set live for the first time. It reported
`recall 0% (0/20) spurious 0 zero-tweaks correct 2/2` and exited cleanly, which
read as "the model extracted nothing", so the first instinct was that the prompt
was wrong. (2) Looked at the model output and found there was none: every call
had failed. (3) Traced why the failures were invisible, and found two separate
causes rather than one.

**What worked** — fixing both. `extract_modifications` returned `[]` for a
failed call and for a genuine empty answer, so the harness could not tell an
outage from a correct refusal. And the harness called `ex.extract_modification`,
renamed to `extract_modifications` two commits earlier; attribute lookup on a
live object fails only at call time, and the surrounding `except Exception`
recorded the `AttributeError` as an empty answer.

**Signal for next time** — the `2/2` was the tell, and it looked like the good
news. When a total failure produces a *plausible* number rather than an obviously
broken one, the reporting layer is conflating states. Ask what the metric would
show if every call failed; if the answer is not "an error", the metric is not
measuring what it claims. The rename half of this was already recorded in this
file one commit earlier and still recurred, which says a written lesson is not a
control: the source-level test that now asserts every extractor method the
harness calls is defined is the control.

## A run that stalled for minutes with no output

**Attempts** — (1) Assumed the model was slow and waited. (2) Waited again when
asked a second time, still with no output to reason about. (3) Checked the
client configuration and found the cause immediately.

**What worked** — reading the defaults I had never set. The retry loop here does
three attempts, and the OpenAI SDK's default `max_retries` of 2 nested three
more inside each one: nine requests per review. The SDK default read timeout is
600 seconds, so a worst case fixture could occupy 90 minutes and 120 fixtures
could not finish in a working day. Setting `max_retries=0` on the client and a
60 second timeout cut the worst case per fixture from about 5400 seconds to 183.

**Signal for next time** — two symptoms were treated as one problem. The stall
was a configuration bug; the *inability to tell whether it had stalled* was a
separate, worse bug, and it is why the first two attempts produced nothing. A
long-running process with no output is indistinguishable from a hung one, so
progress reporting is not a nicety, it is what makes the other failure
diagnosable. Also: when wrapping a client that has its own retry policy, check
it, because nested retries multiply rather than add.

## An abort destroyed a run that had already succeeded

**Attempts** — (1) Started a 10-run measurement. Run 1 completed. Run 2 hit
Groq's daily token cap, every extraction failed, and the harness aborted. (2)
Went looking for run 1's results and found nothing written.

**What worked** — making the abort break out of the loop instead of returning.
Whatever completed is now scored, logged and written, with the log recording how
many runs finished and why the rest were abandoned. Only a run where nothing at
all completed reports no score.

**Signal for next time** — this was predicted and not acted on. The risk of
writing results only at the end was raised before the run, the offer to fix it
was declined for speed, and it cost roughly 16 minutes of quota that could not be
recovered because the daily cap had by then been spent. When a long job holds all
its output until the end, the question is not whether it will be interrupted but
what will remain when it is. Partial evidence is evidence; discarding it because
something later failed is the same failure to distinguish states as everything
else in this file.

## An evidence log attributed one review's model output to another

**Attempts** — (1) Read the first real run log and noticed two raw responses
looked familiar. (2) Compared all twelve and found four were byte-identical
copies of another fixture's response. (3) Traced it to the capture, not the run.

**What worked** — clearing `last_raw_output` at the start of every call.
The attribute was only overwritten on success, and the harness captured it in a
`finally` block, so a failed extraction recorded whatever the *previous* fixture
had returned, under the failed fixture's name. Every mislabelled entry
corresponded to a `FAILED` row. The log also claimed the responses came "from
the first run", which stopped being true once failed calls contributed nothing,
so entries are now labelled with the run they came from.

**Signal for next time** — this is the project's own thesis inside its evidence
artifact. A field that is only written on success, and read unconditionally,
reports stale data as current with no way to tell. The dangerous part was not the
bug but its plausibility: four wrong attributions in a committed file that was
about to be presented as proof. When a value is cached on an object across calls,
clear it at entry, not at exit; the failure path is the one that skips the exit.

## A generation failure was classified as a bad request

**Attempts** — (1) Treated HTTP 400 as permanently fatal, on the reasoning that a
malformed request will never succeed on retry. (2) A live run lost 11 of 36
extractions to `400 json_validate_failed` with no retries. (3) Noticed the same
fixture succeeded on other runs, which a genuinely bad request could not do.

**What worked** — separating the provider's error *code* from the HTTP status.
`json_validate_failed` means the model failed to emit valid JSON, not that the
request was wrong, so it is retried. Genuine bad requests and auth failures are
still not. One failure named the real cause directly, "max completion tokens
reached before generating a valid document", which was `max_tokens` at 2000
being too small for the five-modification fixture; the cap is now 4000.

**Signal for next time** — a status code is a category, not a diagnosis. The same
400 covered "you sent nonsense" and "I could not finish generating", which need
opposite handling. When a class of error is being treated as permanent, check
whether the same input ever succeeds; if it does, the classification is wrong.
