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
