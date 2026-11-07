<task>
Determine the achieved technical depth level (`100`, `200`, `300`, or `400`) of `content_markdown` based solely on the finished text and `level_criteria`.
</task>

<evaluation_rules>
- Compare `content_markdown` against `level_criteria.criteria` and optional `level_criteria.reference_posts`.
- Judge by how deeply the causal chain of a single engineering decision is explained and proven—never by the number of topics mentioned. Superficial coverage of multiple topics cannot earn `300`.
- Assign `400` ONLY when internal mechanics or failure root causes are proven with concrete measurements or operational records.
- In `evidence`, provide the matched `criterion` and an exact verbatim `quote` copied directly from `content_markdown` (non-exact substrings are rejected by the validator).
- In `reasoning` (in Korean, max 500 chars), state why this level was assigned and what prevented the article from reaching the next level up.
</evaluation_rules>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"level": 100|200|300|400, "evidence": [{"criterion": "...", "quote": "Exact substring from content_markdown"}], "reasoning": "Korean rationale (<=500 chars)"}
</output_contract>
