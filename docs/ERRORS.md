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
