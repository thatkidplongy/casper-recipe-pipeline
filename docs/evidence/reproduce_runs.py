"""Run the REAL pipeline twice on one recipe.

Only the single OpenAI HTTP call is stubbed, because no key is available.
Selection, extraction parsing, validation, edit application, attribution and
file writing are all the production code paths, untouched.
"""
import json, os, re, sys, types
os.environ.setdefault("OPENAI_API_KEY", "stub-value-never-sent-anywhere")
sys.path.insert(0, "src")
from loguru import logger
logger.remove()
from llm_pipeline.pipeline import LLMAnalysisPipeline

# What a model plausibly returns for each review, mirroring the shape of the
# extractions already committed in data/enhanced/.
CANNED = {
    "ice cream scoop": {
        "modification_type": "addition", "reasoning": "An extra egg yolk keeps the cookie chewy.",
        "edits": [{"target": "ingredients", "operation": "add_after", "find": "2 eggs",
                   "add": "1 additional egg yolk"}]},
    "advice of others": {
        "modification_type": "quantity_adjustment",
        "reasoning": "More brown sugar than white makes a chewier, more flavourful cookie.",
        "edits": [{"target": "ingredients", "operation": "replace", "find": "1 cup white sugar",
                   "replace": "0.5 cup white sugar"},
                  {"target": "ingredients", "operation": "replace", "find": "1 cup packed brown sugar",
                   "replace": "1.5 cups packed brown sugar"}]},
    "bit bland": {
        "modification_type": "quantity_adjustment", "reasoning": "More salt lifts a bland cookie.",
        "edits": [{"target": "ingredients", "operation": "replace", "find": "0.5 teaspoon salt",
                   "replace": "1 teaspoon salt"},
                  {"target": "ingredients", "operation": "remove", "find": "1 cup chopped walnuts"}]},
    "whole cup of white sugar": {
        "modification_type": "quantity_adjustment", "reasoning": "Less flour and a dash of cinnamon.",
        # A paraphrased find, which is what a model emits when it does not copy verbatim.
        "edits": [{"target": "ingredients", "operation": "replace", "find": "1 cup sugar",
                   "replace": "1 cup white sugar"},
                  {"target": "ingredients", "operation": "add_after", "find": "3 cups all-purpose flour",
                   "add": "1 dash ground cinnamon"}]},
}

def fake_create(**kwargs):
    prompt = kwargs["messages"][0]["content"]
    review = re.search(r'User Review: "(.*?)"\n', prompt, re.S).group(1)
    for cue, payload in CANNED.items():
        if cue in review:
            body = json.dumps(payload); break
    else:
        body = json.dumps({"modification_type": "addition", "reasoning": "n/a", "edits": []})
    msg = types.SimpleNamespace(content=body)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

out = sys.argv[1]
p = LLMAnalysisPipeline(output_dir=out)
p.tweak_extractor.client.chat.completions.create = fake_create
r = p.process_single_recipe("data/recipe_10813_best-chocolate-chip-cookies.json", save_output=True)
sel = r.modifications_applied[0].source_review.text[:60]
print(f"  selected review : {sel!r}")
print(f"  changes reported: {r.enhancement_summary.total_changes}")
