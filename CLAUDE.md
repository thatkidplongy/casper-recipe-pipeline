# CLAUDE.md

Engineering standards for this repository. These are the owner's standards and they apply to
every change in every session. When a request conflicts with a rule here, stop and raise the
conflict rather than resolving it silently.

---

## Project context

A pipeline that enhances recipes by applying community-tested modifications from AllRecipes,
producing an enhanced recipe with line-level attribution back to the source review.

| Path | Contents |
| --- | --- |
| `src/llm_pipeline/` | The pipeline: models, prompts, extractor, modifier, generator, orchestrator |
| `src/scraper_v2.py` | Scraper. **Out of scope.** |
| `src/test_pipeline.py` | Manual demo runner, not a test suite |
| `data/recipe_*.json` | Scraped sample corpus, 6 recipes |
| `data/enhanced/`, `src/data/enhanced/` | Committed outputs, two different schemas, neither reproducible by current code |
| `docs/pipeline-audit.md` | Read-only audit of the inherited MVP. Read this before changing pipeline code. |

Run with `uv`. Python 3.13+. The pipeline needs `OPENAI_API_KEY`; it is read from `.env` via
`load_dotenv()`, falling through to the process environment. `.env` is gitignored and must stay
that way.

---

## Process

- **Red, Green, Refactor for every change.** Write the failing test or eval case first. Show it
  failing. Write the minimum code to pass. Then refactor with everything green.
  **Never refactor while red.**
- **Prompts are code.** A golden dataset of fixtures as JSON lives next to the code, and it exists
  *before* any prompt change.
  - A prompt change or a model change is a behaviour change.
  - Rerun the golden set **10 times** and report the pass rate. Never report "it worked".
  - Prompt text and model id live in source and are versioned with the diff. Neither belongs in
    config that drifts from the code it steers.
- **Simplest solution first.** No abstractions that were not asked for.
- **Do not touch code unrelated to the task.**
- **Ask, don't assume.** If scope or intent is unclear, ask before writing code.
- **Before any significant change, present 2 or 3 approaches** with trade-offs and a
  recommendation, then **wait for a go**. Do not begin implementing while the question is open.

## Quality

Every change passes three layers, and **each layer is reported explicitly, including when a layer
has nothing to report**. "Nothing for this layer" is a required answer, not an omission.

1. **Code quality.** Readability, naming, dead code, error handling, test coverage of the change.
2. **Application protection.** Injection through untrusted input, PII, secrets in logs, unsafe
   handling. In this codebase the untrusted input is scraped review text, and it reaches an LLM
   prompt directly.
3. **Infrastructure.** Dependencies, lockfile, pinned versions, environment and secrets, cost
   exposure. Every added LLM call is a recurring cost and belongs in this layer.

Design:

- **SOLID.**
- **Name the GoF pattern behind any structural change and say why it fits, or state that none
  applies.** "None applies" is a valid and often correct answer.

After every coding task, output these four lines:

```
Files changed:
What was modified: (one line per file)
Files intentionally not touched:
Follow-up needed:
```

## Records

- **`docs/DECISIONS.md`** records every significant decision as **What / Why / Rejected**. The
  rejected alternatives are the point; a decision without them is not recorded.
- **`docs/ERRORS.md`** records any approach that took more than 2 attempts, and what finally
  worked.
- **Conventional commits. One logical change per commit.**
- **The full diff is reviewed before every commit.** Present the diff and wait.

## Hard stops

These are absolute. No inference, no exception, no "it seemed implied".

- **Never print `.env` or any secret.** Not in output, not in logs, not in a commit, not redacted
  "just to confirm the shape".
- **Never push, delete files, or call any external API** other than the pipeline's own OpenAI
  calls, **without an explicit yes in the same message**. Approval does not carry across
  messages, and approval for one action is not approval for the next.
- **The scraper and the UI are out of scope.** Anything found there is recorded as future work,
  not fixed.

---

## Standing notes

- `.gitignore:52` ignores `claude.md`. Git is case-sensitive on Linux, so `CLAUDE.md` is tracked
  here. On a case-insensitive filesystem with `core.ignorecase=true`, that pattern would swallow
  this file. Flagged, not changed.
- The pipeline's only untrusted input is scraped review text, and it is interpolated into a prompt
  without delimiting or escaping. Treat it as untrusted in layer 2 of every review that touches
  the extractor.
