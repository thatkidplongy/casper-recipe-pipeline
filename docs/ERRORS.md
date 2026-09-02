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
