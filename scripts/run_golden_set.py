#!/usr/bin/env python3
"""Run the golden tweak set against the extractor and report a pass rate.

Two modes:
  live   real OpenAI calls, requires OPENAI_API_KEY (from .env or the environment)
  stub   canned extractions, no network, produces a floor rather than a measurement

Usage from the repo root:
    uv run python scripts/run_golden_set.py --runs 10
    uv run python scripts/run_golden_set.py --runs 10 --stub
    uv run python scripts/run_golden_set.py --runs 10 --model gpt-4o-mini

What is measured, per fixture:
  recall over EXACT expectations   modifications the reviewer quantified
  underspecified, reported apart   the change was made but no amount given, so
                                   declining to invent one is correct behaviour
  spurious edits                   edits matching nothing the reviewer asked for
  zero-tweak correctness           the two fixtures describing no change at all;
                                   correct only when the extractor returns nothing
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_transcript import redact  # shared redactor, so the log cannot leak

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "src" / "llm_pipeline" / "fixtures" / "golden_tweaks.json"
ANCHOR_THRESHOLD = 0.6


def load_fixtures(path=FIXTURES):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _norm(s):
    return " ".join((s or "").lower().split())


def matches_anchor(edit, anchor, target):
    """Does this edit act on the recipe line the expectation anchors to?"""
    if not anchor or edit.get("target") != target:
        return False
    find = _norm(edit.get("find"))
    a = _norm(anchor)
    if not find:
        return False
    if find in a or a in find:
        return True
    return SequenceMatcher(None, find, a).ratio() >= ANCHOR_THRESHOLD


class AllCallsFailed(RuntimeError):
    """Every extraction in a run raised. The run has no result to report.

    Without this the harness scored a total outage as a set of empty answers,
    which credited the two zero-modification fixtures for being "correct" and
    reported a recall figure that described nothing.
    """


def score_fixture(fixture, edits, failed=False):
    """Score one fixture against the edits an extraction produced.

    Args:
        fixture: The golden-set entry
        edits: Edits the extraction returned
        failed: True when the extraction call raised. An empty result from a
            failed call is not an answer, so it is never credited.
    """
    expected = fixture["expected_modifications"]
    exact = [m for m in expected if m["specificity"] == "exact"]
    under = [m for m in expected if m["specificity"] != "exact"]

    claimed = set()
    exact_found = under_found = 0
    missed = []

    for group, is_exact in ((exact, True), (under, False)):
        for m in group:
            hit = next(
                (i for i, e in enumerate(edits)
                 if i not in claimed and matches_anchor(e, m["anchor"], m["target"])),
                None,
            )
            if hit is not None:
                claimed.add(hit)
                if is_exact:
                    exact_found += 1
                else:
                    under_found += 1
            elif is_exact:
                missed.append({"id": m["id"], "intent": m["intent"], "anchor": m["anchor"]})

    spurious_edits = [e for i, e in enumerate(edits) if i not in claimed]
    is_zero = len(expected) == 0

    return {
        "tweak_id": fixture["tweak_id"],
        "failed": failed,
        "exact_expected": len(exact),
        "exact_found": exact_found,
        "underspec_expected": len(under),
        "underspec_found": under_found,
        "spurious": len(spurious_edits),
        "spurious_finds": [e.get("find") for e in spurious_edits],
        "missed": missed,
        "is_zero_tweak": is_zero,
        # A failed call returning nothing is not the same as a model correctly
        # declining to extract. Crediting it is how a total outage scored 2/2.
        "zero_tweak_correct": (len(edits) == 0 and not failed) if is_zero else None,
        "edits_returned": len(edits),
    }


def run_fixtures(fixtures, recipes, extract, on_result=None):
    """Score every fixture once, and refuse to report a run that wholly failed.

    Args:
        on_result: called with each fixture's result as it completes, so a long
            run reports progress instead of looking like a hang.

    Returns:
        (results, errors) where errors is a list of (tweak_id, message)

    Raises:
        AllCallsFailed: when every extraction raised. A run in which nothing
            succeeded is an error, not a score of zero.
    """
    results, errors = [], []

    for fixture in fixtures:
        try:
            edits = extract(fixture, recipes.get(fixture["recipe_id"], {}))
            result = score_fixture(fixture, edits)
        except Exception as exc:
            errors.append((fixture["tweak_id"], f"{type(exc).__name__}: {exc}"))
            result = score_fixture(fixture, [], failed=True)

        results.append(result)
        if on_result:
            on_result(result)

    if errors and len(errors) == len(fixtures):
        first = errors[0]
        raise AllCallsFailed(
            f"all {len(fixtures)} extractions failed; first was "
            f"{first[0]}: {first[1]}"
        )

    return results, errors


def aggregate(runs):
    """Fold per-run fixture results into one summary."""
    flat = [r for run in runs for r in run]
    ee = sum(r["exact_expected"] for r in flat)
    ef = sum(r["exact_found"] for r in flat)
    ue = sum(r["underspec_expected"] for r in flat)
    uf = sum(r["underspec_found"] for r in flat)
    zero = [r for r in flat if r["is_zero_tweak"]]
    return {
        "runs": len(runs),
        "failed": sum(1 for r in flat if r.get("failed")),
        "exact_expected": ee,
        "exact_found": ef,
        "recall": (ef / ee) if ee else 0.0,
        "underspec_expected": ue,
        "underspec_found": uf,
        "spurious": sum(r["spurious"] for r in flat),
        "zero_tweaks": len(zero),
        "zero_tweaks_correct": sum(1 for r in zero if r["zero_tweak_correct"]),
    }


# --------------------------------------------------------------------------
# Extraction back ends
# --------------------------------------------------------------------------

def _edits_of(modification):
    """Normalise a ModificationObject (or dict) into a list of edit dicts."""
    if modification is None:
        return []
    if hasattr(modification, "model_dump"):
        modification = modification.model_dump()
    return list(modification.get("edits") or [])


def live_extractor(model, raw_sink=None):
    """Build the live extraction callable.

    Args:
        raw_sink: optional dict; each fixture's raw model response is stored
            under its tweak_id, so the run log can show what actually came back.
    """
    from llm_pipeline.tweak_extractor import TweakExtractor
    from llm_pipeline.models import Recipe, Review

    ex = TweakExtractor(model=model)
    live_extractor.resolved = (ex.model, ex.endpoint)

    def run(fixture, recipe_data):
        recipe = Recipe(
            recipe_id=recipe_data["recipe_id"], title=recipe_data["title"],
            ingredients=recipe_data["ingredients"], instructions=recipe_data["instructions"],
        )
        review = Review(text=fixture["review_text"], rating=fixture["review_rating"],
                        has_modification=True)
        # extract_modifications returns a list, one entry per discrete
        # modification, so the edits of all of them are flattened here.
        try:
            mods = ex.extract_modifications(review, recipe)
        finally:
            if raw_sink is not None and ex.last_raw_output:
                raw_sink.setdefault(fixture["tweak_id"], ex.last_raw_output)
        return [e for mod in mods for e in _edits_of(mod)]

    return run


def stub_extractor():
    """A deliberately competent stub: returns the exact expectations.

    It is a control, not a measurement. It shows what the scorer reports when
    extraction is perfect, so a live number can be read against a known ceiling.
    """
    def run(fixture, recipe_data):
        return [
            {"target": m["target"], "operation": "replace", "find": m["anchor"],
             "replace": "STUB", "add": None}
            for m in fixture["expected_modifications"]
            if m["specificity"] == "exact" and m["anchor"]
        ]
    return run


def per_fixture_line(tweak_id, p):
    """One report line per fixture.

    A fixture whose only expectations are underspecified has an empty recall
    denominator without being a zero-tweak, so it gets its own wording rather
    than a division.
    """
    if p["z"]:
        return (f"  {tweak_id:<12} zero-tweak, returned nothing in {p['zc']}/{p['zn']} runs, "
                f"{p['sp']} invented edits")
    if p["e"] == 0:
        return (f"  {tweak_id:<12} no exact expectations (all underspecified), "
                f"{p['sp']} spurious")
    return f"  {tweak_id:<12} recall {p['f']}/{p['e']} = {p['f'] / p['e']:.0%}, {p['sp']} spurious"


def configure_logging(verbose=False):
    """Set the pipeline's log level for a run, and report what was chosen.

    Raw model responses are written to the run log, so echoing them to stdout
    only buries the per-fixture progress a viewer is following. --verbose brings
    them back for debugging.
    """
    level = "DEBUG" if verbose else "WARNING"
    try:
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level=level,
                   format="<level>{level: <8}</level> | {message}")
    except Exception:
        pass
    return level


def display_path(path):
    """Path for printing: relative to the repo when inside it, absolute if not."""
    path = Path(path)
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _git(*args):
    """Best-effort git query; returns None outside a repository."""
    try:
        out = subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def render_run_log(meta, runs, errors, raw_outputs):
    """Render a self-contained record of one measurement.

    Everything is passed through the transcript redactor, so a key that reached
    an endpoint string or an error message cannot reach the committed log.
    """
    agg = aggregate(runs)
    lines = [
        "# Golden set run",
        "",
        "Produced by `scripts/run_golden_set.py`. Credentials are redacted.",
        "",
        "## Provenance",
        "",
        "| | |",
        "| --- | --- |",
        f"| Started | {meta['started_at']} |",
        f"| Commit | `{meta['commit']}` |",
        f"| Mode | {meta['mode']} |",
        f"| Model | `{meta['model']}` |",
        f"| Endpoint | {meta['endpoint']} |",
        f"| Fixtures | {meta['fixtures']} |",
        f"| Runs requested | {meta['runs']} |",
        f"| Runs completed | {meta.get('completed_runs', len(runs))} |",
        "",
    ]
    completed = meta.get("completed_runs", len(runs))
    if meta.get("aborted"):
        lines += [
            f"> **INCOMPLETE: {completed} of {meta['runs']} runs completed.** "
            f"The remainder was abandoned: {meta['aborted']}",
            ">",
            "> The figures below describe only the completed runs. Treat them as "
            "provisional: fewer repetitions show less variance than the standard "
            "asks for.",
            "",
        ]
    if meta.get("dirty"):
        lines += ["> **The working tree had uncommitted changes when this ran.** "
                  "The commit above does not fully describe the code that produced "
                  "these numbers.", ""]

    lines += [
        "## Summary",
        "",
        "| | |",
        "| --- | --- |",
        f"| Recall on exact modifications | {agg['recall']:.1%} "
        f"({agg['exact_found']}/{agg['exact_expected']}) |",
        f"| Underspecified recovered (not required) | "
        f"{agg['underspec_found']}/{agg['underspec_expected']} |",
        f"| Spurious edits | {agg['spurious']} |",
        f"| Zero-modification fixtures correct | "
        f"{agg['zero_tweaks_correct']}/{agg['zero_tweaks']} |",
        f"| Failed extractions | {agg['failed']} |",
        "",
    ]
    if agg["failed"]:
        lines += ["> **Degraded run.** Some extractions failed, so recall "
                  "understates the model and these figures are not a clean "
                  "measurement.", ""]

    lines += ["## Per fixture, per run", "",
              "| Run | Fixture | Exact found | Spurious | Zero-tweak | Failed |",
              "| --- | --- | --- | --- | --- | --- |"]
    for n, run in enumerate(runs, 1):
        for r in run:
            zero = "-" if not r["is_zero_tweak"] else (
                "correct" if r["zero_tweak_correct"] else "INVENTED")
            lines.append(
                f"| {n} | `{r['tweak_id']}` | {r['exact_found']}/{r['exact_expected']} "
                f"| {r['spurious']} | {zero} | {'YES' if r['failed'] else ''} |")
    lines.append("")

    if errors:
        lines += ["## Failures", ""]
        for tid, msg in errors:
            lines.append(f"- `{tid}`: {msg}")
        lines.append("")

    # What was missed and what was invented, named rather than counted. A
    # reader deciding whether to trust the number needs the disagreement itself.
    detail = []
    for n, run in enumerate(runs, 1):
        for r in run:
            if r["failed"] or (not r["missed"] and not r["spurious_finds"]):
                continue
            detail.append((n, r))
    if detail:
        lines += ["## What was missed and what was invented", ""]
        for n, r in detail:
            lines.append(f"**Run {n}, `{r['tweak_id']}`**")
            lines.append("")
            for m in r["missed"]:
                anchor = f" (expected at `{m['anchor']}`)" if m.get("anchor") else ""
                lines.append(f"- Not found: {m['intent']}{anchor}")
            for find in r["spurious_finds"]:
                lines.append(f"- Produced but not expected: `{find}`")
            lines.append("")

    if raw_outputs:
        lines += ["## Raw model responses", "",
                  "Verbatim, from the first run. A summary is a claim about a "
                  "response; this is the response.", ""]
        for tid, raw in raw_outputs.items():
            lines += [f"### `{tid}`", "", "```json", raw.strip(), "```", ""]

    return redact("\n".join(lines)) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=10, help="repetitions (default 10)")
    ap.add_argument("--stub", action="store_true", help="no network; control run")
    ap.add_argument("--model", default=None,
                    help="model id; defaults to LLM_MODEL, then the documented default")
    ap.add_argument("--base-url", default=None,
                    help="OpenAI-compatible endpoint; defaults to LLM_BASE_URL")
    ap.add_argument("--out", default="docs/evidence/golden_set_report.json")
    ap.add_argument("--verbose", action="store_true",
                    help="echo raw model responses to the terminal "
                         "(they are always written to the run log)")
    ap.add_argument("--log", default=None,
                    help="path for the Markdown run log "
                         "(default docs/evidence/golden_set_run_<timestamp>.md)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(REPO / "src"))
    configure_logging(args.verbose)
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")

    if not args.stub and not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
        ap.error(
            "No API key found. Set LLM_API_KEY or OPENAI_API_KEY in .env at the repo "
            "root, or export it, or pass --stub for a no-network control run. Any "
            "OpenAI-compatible endpoint works; set LLM_BASE_URL to use one."
        )

    data = load_fixtures()
    fixtures = data["fixtures"]
    recipes = {}
    for f in (REPO / "data").glob("recipe_*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        recipes[d["recipe_id"]] = d

    if args.base_url:
        os.environ["LLM_BASE_URL"] = args.base_url
    raw_outputs = {}
    extract = (stub_extractor() if args.stub
               else live_extractor(args.model, raw_sink=raw_outputs))
    if args.stub:
        mode = "STUB (control, not a measurement)"
    else:
        # Taken from the client itself, so the report cannot claim one endpoint
        # while requests went to another.
        resolved_model, resolved_endpoint = live_extractor.resolved
        mode = f"LIVE {resolved_model} @ {resolved_endpoint}"
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"mode: {mode}   fixtures: {len(fixtures)}   runs: {args.runs}\n")

    runs = []
    all_errors = []
    aborted = None
    for n in range(1, args.runs + 1):
        def progress(r, _n=n):
            if r["failed"]:
                mark = "FAILED"
            elif r["is_zero_tweak"]:
                mark = "ok, returned nothing" if r["zero_tweak_correct"] else \
                       f"INVENTED {r['spurious']} edit(s)"
            else:
                mark = f"{r['exact_found']}/{r['exact_expected']} found"
                if r["spurious"]:
                    mark += f", {r['spurious']} spurious"
            print(f"    run {_n:>2} {r['tweak_id']:<12} {mark}", flush=True)

        try:
            results, errors = run_fixtures(fixtures, recipes, extract, on_result=progress)
        except AllCallsFailed as exc:
            print(f"\n  run {n}: ABORTED. {exc}")
            aborted = f"run {n}: {exc}"
            if runs:
                # Earlier runs completed. Discarding them because a later run
                # failed would destroy a measurement that actually happened.
                print(f"\n  {len(runs)} earlier run(s) completed and are being kept.")
            else:
                print("\nNo score is reported. Every extraction failed, so there is\n"
                      "nothing to measure. Fix the cause and rerun.")
            break

        runs.append(results)
        all_errors.extend((tid, msg) for tid, msg in errors)
        a = aggregate([results])
        suffix = f"  FAILED CALLS {a['failed']}" if a["failed"] else ""
        print(f"  run {n:>2}: recall {a['recall']:.0%}  "
              f"({a['exact_found']}/{a['exact_expected']})  "
              f"spurious {a['spurious']}  "
              f"zero-tweaks correct {a['zero_tweaks_correct']}/{a['zero_tweaks']}"
              f"{suffix}")

    if not runs:
        return 2

    agg = aggregate(runs)
    print("\n" + "=" * 62)
    if aborted:
        print(f"  INCOMPLETE: {len(runs)} of {args.runs} runs completed")
    print(f"  runs                       {agg['runs']}")
    print(f"  recall on exact mods       {agg['recall']:.1%}  ({agg['exact_found']}/{agg['exact_expected']})")
    print(f"  underspecified recovered   {agg['underspec_found']}/{agg['underspec_expected']}  (not required)")
    print(f"  spurious edits             {agg['spurious']}")
    print(f"  zero-tweaks correct        {agg['zero_tweaks_correct']}/{agg['zero_tweaks']}")
    print(f"  failed extractions         {agg['failed']}")
    print("=" * 62)
    if agg["failed"]:
        print("  DEGRADED RUN. Some extractions failed, so recall understates the\n"
              "  model and the figures above are not a clean measurement.")
        for tid, msg in all_errors[:5]:
            print(f"    {tid}: {msg}")
    if args.stub:
        print("  STUB RUN. Every number here is a ceiling, not a measurement.")

    print("\nper fixture, worst first:")
    per = {}
    for run in runs:
        for r in run:
            p = per.setdefault(r["tweak_id"], {"e": 0, "f": 0, "sp": 0, "z": r["is_zero_tweak"], "zc": 0, "zn": 0})
            p["e"] += r["exact_expected"]; p["f"] += r["exact_found"]; p["sp"] += r["spurious"]
            if r["is_zero_tweak"]:
                p["zn"] += 1; p["zc"] += 1 if r["zero_tweak_correct"] else 0
    for tid, p in sorted(per.items(), key=lambda kv: (kv[1]["f"] / kv[1]["e"]) if kv[1]["e"] else 1.0):
        print(per_fixture_line(tid, p))

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"mode": mode, "runs": args.runs, "summary": agg,
         "errors": [{"tweak_id": t, "error": m} for t, m in all_errors],
         "per_run": runs}, indent=2) + "\n", encoding="utf-8")
    print(f"\nreport: {display_path(out)}")

    meta = {
        "mode": mode,
        "model": "stub" if args.stub else live_extractor.resolved[0],
        "endpoint": "none" if args.stub else live_extractor.resolved[1],
        "commit": _git("rev-parse", "--short", "HEAD") or "unknown",
        "started_at": started_at,
        "runs": args.runs,
        "completed_runs": len(runs),
        "aborted": aborted,
        "fixtures": len(fixtures),
        "dirty": bool(_git("status", "--porcelain")),
    }
    log_path = REPO / (args.log or
                       f"docs/evidence/golden_set_run_"
                       f"{started_at.replace(':', '').replace('-', '')}.md")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(render_run_log(meta, runs, all_errors, raw_outputs),
                        encoding="utf-8")
    print(f"run log: {display_path(log_path)}")

    if aborted:
        return 2
    return 1 if agg["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
