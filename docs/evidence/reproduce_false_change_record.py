"""Exercise the REAL RecipeModifier against the REAL recipe. No LLM involved."""
import json, sys
sys.path.insert(0, "src")
from llm_pipeline.models import ModificationObject, ModificationEdit, Recipe
from llm_pipeline.recipe_modifier import RecipeModifier
from loguru import logger
logger.remove()

d = json.load(open("data/recipe_10813_best-chocolate-chip-cookies.json"))
recipe = Recipe(recipe_id=d["recipe_id"], title=d["title"],
                ingredients=d["ingredients"], instructions=d["instructions"])
mod = RecipeModifier()

cases = [
    ("exact substring, genuinely applies", "1 cup white sugar", "0.5 cup white sugar"),
    ("paraphrased, fuzzy-matches at 0.79", "1 cup sugar", "0.5 cup sugar"),
    ("reordered, fuzzy-matches at 0.63", "white sugar, 1 cup", "0.5 cup white sugar"),
    ("instruction temperature change", "350 degrees F", "375 degrees F"),
]

print(f"{'case':<38} {'record?':<8} {'text changed?':<14} from -> to")
print("-" * 118)
for label, find, repl in cases:
    target = "instructions" if "350" in find else "ingredients"
    edit = ModificationEdit(target=target, operation="replace", find=find, replace=repl)
    content = recipe.instructions if target == "instructions" else recipe.ingredients
    new_content, records = mod.apply_edit(edit, content)
    if not records:
        print(f"{label:<38} {'no':<8} {'n/a':<14} edit silently dropped")
        continue
    r = records[0]
    changed = r.from_text != r.to_text
    flag = "" if changed else "   <-- FALSE RECORD"
    print(f"{label:<38} {'YES':<8} {str(changed):<14} {r.from_text[:34]!r} -> {r.to_text[:34]!r}{flag}")

print()
print("Now the same through apply_modification, which is what the pipeline calls:")
m = ModificationObject(
    modification_type="quantity_adjustment",
    reasoning="Halve the sugar for a less sweet cookie.",
    edits=[ModificationEdit(target="ingredients", operation="replace",
                            find="1 cup sugar", replace="0.5 cup sugar")],
)
modified, records = mod.apply_modification(recipe, m)
print(f"  changes reported : {len(records)}")
for r in records:
    print(f"  from_text        : {r.from_text!r}")
    print(f"  to_text          : {r.to_text!r}")
    print(f"  identical        : {r.from_text == r.to_text}")
print(f"  ingredient line unchanged in output: {modified.ingredients[1]!r}")
print()
print("  validate_modification_safety would have caught it, but nothing calls it:")
safe, warnings = mod.validate_modification_safety(m, recipe)
print(f"    is_safe={safe}  warnings={warnings}")
