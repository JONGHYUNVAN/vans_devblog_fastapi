# Task: Minimal Korean Copy-Editing (`polish_tone`)

## Step 1: Identify Defective Paragraphs Only
Scan `paragraphs` (`index`, `text`) and select ONLY paragraphs with severe translationese (e.g., broken double-subject constructions or unnatural double passives like "~되어진다") or grammatical agreement errors.
- Do NOT rewrite natural sentences just because a system component, query, or thread is the grammatical subject.
- Do NOT split paragraphs or break sentences apart mechanically.
- If a paragraph reads naturally, skip it. If no paragraphs require fixes, output `"patches": []`.

## Step 2: Enforce Validator Invariants
Every patch in `patches` MUST obey these three invariants (violations are automatically discarded):
1. Do not add or remove technical information.
2. Keep all inline code (`` `...` ``), numbers, URLs, and section anchors (`<!-- sec:id -->`) identical character-for-character.
3. Keep the exact count of Korean negation substrings (`않`, `못`, `없`) unchanged within each paragraph.

## Output Format
Return ONLY a single raw JSON object (no markdown fences):
{"patches": [{"index": 0, "text": "Edited Korean paragraph"}]}
