"""
LLM prompts and examples for recipe modification extraction.

This module contains carefully crafted prompts for extracting structured
modifications from user review text.
"""

SYSTEM_PROMPT = """You are an expert recipe analyst. Your job is to extract structured recipe modifications from user reviews.

When a user shares their experience modifying a recipe, you need to:
1. Identify exactly what changes they made
2. Understand why they made those changes
3. Convert their modifications into structured edit operations

A review often describes SEVERAL discrete modifications. "I added an egg and
halved the sugar" is two: one addition and one quantity adjustment. Each gets its
own entry, with its own category and its own reasoning. Do not merge them, and do
not stop after the first.

You must output valid JSON: an object with a "modifications" array.

Categories:
- "ingredient_substitution": Replacing one ingredient with another
- "quantity_adjustment": Changing amounts of existing ingredients
- "technique_change": Altering cooking method, temperature, time
- "addition": Adding new ingredients or steps
- "removal": Removing ingredients or steps

Edit operations:
- "replace": Find existing text and replace it
- "add_after": Add new text after finding target text
- "remove": Remove text that matches the find pattern

Be precise with text matching - use the exact text from the original recipe when possible."""

EXTRACTION_PROMPT = """Original Recipe:
Title: {title}
Ingredients: {ingredients}
Instructions: {instructions}

User Review: "{review_text}"

Extract the recipe modifications from this review. The user has made changes to improve the recipe.

Output a JSON object with this structure:
{{
    "modification_type": "quantity_adjustment|ingredient_substitution|technique_change|addition|removal",
    "reasoning": "Brief explanation of why this modification improves the recipe",
    "edits": [
        {{
            "target": "ingredients|instructions",
            "operation": "replace|add_after|remove",
            "find": "exact text to find",
            "replace": "replacement text (for replace operations)",
            "add": "text to add (for add_after operations)"
        }}
    ]
}

Focus on concrete changes the user actually made, not general suggestions."""

FEW_SHOT_EXAMPLES = [
    {
        "review": "I used a half cup of sugar and one-and-a-half cups of brown sugar instead of the recipe amounts. Made the cookies much more chewy and flavorful!",
        "ingredients": [
            "1 cup butter, softened",
            "1 cup white sugar",
            "1 cup packed brown sugar",
            "2 eggs",
        ],
        "expected_output": {
            "modification_type": "quantity_adjustment",
            "reasoning": "Makes cookies more chewy and flavorful by increasing brown sugar ratio",
            "edits": [
                {
                    "target": "ingredients",
                    "operation": "replace",
                    "find": "1 cup white sugar",
                    "replace": "0.5 cup white sugar",
                },
                {
                    "target": "ingredients",
                    "operation": "replace",
                    "find": "1 cup packed brown sugar",
                    "replace": "1.5 cups packed brown sugar",
                },
            ],
        },
    },
    {
        "review": "I added a teaspoon of cream of tartar to the batter and omitted the water. The cookies retained their shape and didn't spread when baked.",
        "ingredients": [
            "1 teaspoon baking soda",
            "2 teaspoons hot water",
            "0.5 teaspoon salt",
        ],
        "expected_output": {
            "modification_type": "addition",
            "reasoning": "Helps cookies retain shape and prevents spreading during baking",
            "edits": [
                {
                    "target": "ingredients",
                    "operation": "add_after",
                    "find": "0.5 teaspoon salt",
                    "add": "1 teaspoon cream of tartar",
                },
                {
                    "target": "ingredients",
                    "operation": "remove",
                    "find": "2 teaspoons hot water",
                },
            ],
        },
    },
    {
        "review": "I used 1 tsp of salt instead of 1/2 tsp and omitted the nuts. Much better flavor without being too salty.",
        "ingredients": ["0.5 teaspoon salt", "1 cup chopped walnuts"],
        "expected_output": {
            "modification_type": "quantity_adjustment",
            "reasoning": "Improves flavor balance without making cookies too salty",
            "edits": [
                {
                    "target": "ingredients",
                    "operation": "replace",
                    "find": "0.5 teaspoon salt",
                    "replace": "1 teaspoon salt",
                },
                {
                    "target": "ingredients",
                    "operation": "remove",
                    "find": "1 cup chopped walnuts",
                },
            ],
        },
    },
    {
        "review": "I baked them at 375 degrees instead of 350 for about 8-9 minutes. They came out perfectly crispy on the edges.",
        "instructions": [
            "Preheat the oven to 350 degrees F (175 degrees C)",
            "Bake in the preheated oven until edges are nicely browned, about 10 minutes",
        ],
        "expected_output": {
            "modification_type": "technique_change",
            "reasoning": "Higher temperature and shorter time creates crispier edges",
            "edits": [
                {
                    "target": "instructions",
                    "operation": "replace",
                    "find": "350 degrees F",
                    "replace": "375 degrees F",
                },
                {
                    "target": "instructions",
                    "operation": "replace",
                    "find": "about 10 minutes",
                    "replace": "about 8-9 minutes",
                },
            ],
        },
    },
]


def build_few_shot_prompt(
    review_text: str, title: str, ingredients: list, instructions: list
) -> str:
    """Build a few-shot prompt with examples for better extraction accuracy."""

    examples_text = "\n\n".join(
        [
            f"Example {i + 1}:\n"
            f'Review: "{example["review"]}"\n'
            f"Output: {example['expected_output']}"
            for i, example in enumerate(
                FEW_SHOT_EXAMPLES[:2]
            )  # Use 2 most relevant examples
        ]
    )

    prompt = f"""{SYSTEM_PROMPT}

Here are some examples of how to extract modifications:

{examples_text}

Now extract from this review:

{
        EXTRACTION_PROMPT.format(
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            review_text=review_text,
        )
    }"""

    return prompt


def build_simple_prompt(
    review_text: str, title: str, ingredients: list, instructions: list
) -> str:
    """Build the extraction prompt.

    Asks for every discrete modification separately. A review describing four
    numbered tweaks must come back as four entries, not one.
    """
    return f"""{SYSTEM_PROMPT}

Original Recipe:
Title: {title}
Ingredients: {ingredients}
Instructions: {instructions}

User Review: "{review_text}"

Extract EVERY discrete modification the reviewer actually made. Separate them:
one entry per change, each with its own category and its own reasoning.

Worked example. The review "I added an egg and halved the sugar because they were
too sweet" contains TWO discrete modifications, so it returns two entries: an
"addition" for the egg, and a "quantity_adjustment" for the sugar. Returning one
entry would lose a change the reviewer made.

Rules:
- One entry per discrete change. Four numbered tweaks means four entries.
- Only changes the reviewer actually made. A preference they did not act on
  ("I would prefer more apple"), something they plan for next time ("next time I
  will use fresh ginger"), or advice offered to other cooks is NOT a
  modification. If the reviewer describes no change they made, return an empty
  list.
- Do not invent an amount the reviewer did not give. If they say "extra soy
  sauce" with no quantity, describe the change without fabricating a number.
- Copy `find` text verbatim from the recipe above so it can be located.

Output a JSON object with this structure:
{{
    "modifications": [
        {{
            "modification_type": "quantity_adjustment|ingredient_substitution|technique_change|addition|removal",
            "reasoning": "Why this specific change improves the recipe",
            "edits": [
                {{
                    "target": "ingredients|instructions",
                    "operation": "replace|add_after|remove",
                    "find": "exact text to find, copied from the recipe",
                    "replace": "replacement text (for replace operations)",
                    "add": "text to add (for add_after operations)"
                }}
            ]
        }}
    ]
}}

Focus on concrete changes the user actually made, not general suggestions."""
