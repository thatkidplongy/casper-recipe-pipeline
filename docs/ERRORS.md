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
