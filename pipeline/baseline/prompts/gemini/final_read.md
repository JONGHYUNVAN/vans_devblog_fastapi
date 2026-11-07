# Task: Pre-Publication Final Read

## Step 1: Inspect Internal Coherence
Read `content_markdown` from start to finish against `topic.core_mechanism` and `topic.out_of_scope` (ignore external spec accuracy and writing style):
1. `BODY_CODE_CONTRADICTION` (Blocking): Prose and code conflict, code references unexplained values, or a constraint in the "before" code silently disappeared in the "after" code.
2. `CORE_MECHANISM_MISSING` (Blocking): The article and code fail to demonstrate what the title and `topic.core_mechanism` promised (do not flag exclusions listed in `topic.out_of_scope`).
3. `OBSERVATION` (Non-blocking): Non-blocking observations for human operators.

## Step 2: Validate Quotes & Anchor IDs
- Every finding must include an existing `section_id` from `content_markdown` and an exact character-for-character substring from `content_markdown` in `quote`.
- Write `comment` and `overall` in Korean. If there are no issues, return `"findings": []`.

## Output Format
Return ONLY a single raw JSON object (no markdown fences):
{"findings": [{"kind": "BODY_CODE_CONTRADICTION"|"CORE_MECHANISM_MISSING"|"OBSERVATION", "section_id": "...", "quote": "Exact substring from content_markdown", "comment": "Korean explanation"}], "overall": "Korean overall summary (<=500 chars)"}
