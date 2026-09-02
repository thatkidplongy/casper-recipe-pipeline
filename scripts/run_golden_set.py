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
import sys
from difflib import SequenceMatcher
from pathlib import Path

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


def run_fixtures(fixtures, recipes, extract):
    """Score every fixture once, and refuse to report a run that wholly failed.

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
            results.append(score_fixture(fixture, edits))
        except Exception as exc:
            errors.append((fixture["tweak_id"], f"{type(exc).__name__}: {exc}"))
            results.append(score_fixture(fixture, [], failed=True))

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


def live_extractor(model):
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
        return [e for mod in ex.extract_modifications(review, recipe)
                for e in _edits_of(mod)]

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
    args = ap.parse_args(argv)

    sys.path.insert(0, str(REPO / "src"))
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
    extract = stub_extractor() if args.stub else live_extractor(args.model)
    if args.stub:
        mode = "STUB (control, not a measurement)"
    else:
        # Taken from the client itself, so the report cannot claim one endpoint
        # while requests went to another.
        resolved_model, resolved_endpoint = live_extractor.resolved
        mode = f"LIVE {resolved_model} @ {resolved_endpoint}"
    print(f"mode: {mode}   fixtures: {len(fixtures)}   runs: {args.runs}\n")

    runs = []
    all_errors = []
    for n in range(1, args.runs + 1):
        try:
            results, errors = run_fixtures(fixtures, recipes, extract)
        except AllCallsFailed as exc:
            print(f"\n  run {n}: ABORTED. {exc}")
            print("\nNo score is reported. Every extraction failed, so there is\n"
                  "nothing to measure. Fix the cause and rerun.")
            return 2

        runs.append(results)
        all_errors.extend((n, tid, msg) for tid, msg in errors)
        a = aggregate([results])
        suffix = f"  FAILED CALLS {a['failed']}" if a["failed"] else ""
        print(f"  run {n:>2}: recall {a['recall']:.0%}  "
              f"({a['exact_found']}/{a['exact_expected']})  "
              f"spurious {a['spurious']}  "
              f"zero-tweaks correct {a['zero_tweaks_correct']}/{a['zero_tweaks']}"
              f"{suffix}")

    agg = aggregate(runs)
    print("\n" + "=" * 62)
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
        for n, tid, msg in all_errors[:5]:
            print(f"    run {n} {tid}: {msg}")
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
         "errors": [{"run": n, "tweak_id": t, "error": m} for n, t, m in all_errors],
         "per_run": runs}, indent=2) + "\n", encoding="utf-8")
    print(f"\nreport: {out.relative_to(REPO)}")
    return 1 if agg["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
