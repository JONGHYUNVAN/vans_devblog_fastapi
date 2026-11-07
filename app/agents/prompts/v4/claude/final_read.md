<task>
Perform a final end-to-end check on `content_markdown` before publication to catch any internal contradictions or unfulfilled core promises introduced during iterative revisions.
</task>

<inspection_criteria>
Do not evaluate external specification facts or writing style. Check strictly for internal consistency against `topic.core_mechanism` and `topic.out_of_scope`:
- `BODY_CODE_CONTRADICTION` (Blocking): Prose explanation and code contradict each other (e.g., prose claims data loss is prevented while the code ignores errors; code uses variables/values whose origin is never explained; a critical constraint in the "before" code silently vanished in the "after" code).
- `CORE_MECHANISM_MISSING` (Blocking): The body and code fail to actually demonstrate what the title and `topic.core_mechanism` promised. (Omitting items in `topic.out_of_scope` is expected and must not be flagged.)
- `OBSERVATION` (Non-blocking): Optional note for human operators that does not block publication.

Rules:
- For each finding, specify a valid `section_id` from the document's anchors and copy-paste an exact verbatim substring from `content_markdown` into `quote` (non-exact quotes are rejected by the validator).
- Write `comment` and `overall` in Korean. If no issues exist, return `"findings": []`.
</inspection_criteria>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"findings": [{"kind": "BODY_CODE_CONTRADICTION"|"CORE_MECHANISM_MISSING"|"OBSERVATION", "section_id": "...", "quote": "Exact substring from content_markdown", "comment": "Korean explanation"}], "overall": "Korean overall summary (<=500 chars)"}
</output_contract>
