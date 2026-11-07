# Task: Technical Depth Level Assessment

## Step 1: Evaluate Against `level_criteria`
Assess `content_markdown` using `level_criteria.criteria` (and `level_criteria.reference_posts` if present):
1. Evaluate the causal depth of a single engineering decision rather than the number of concepts mentioned.
2. Grant level `400` ONLY when internal behavior or failure causes are backed by concrete measurements or operational records.

## Step 2: Extract Verbatim Evidence & Reasoning
1. In `evidence`, record each satisfied `criterion` and copy-paste an exact verbatim substring from `content_markdown` into `quote`.
2. In `reasoning` (Korean, <= 500 chars), explain why the article qualifies for this level and why it falls short of the next higher level.

## Output Format
Return ONLY a single raw JSON object (no markdown fences):
{"level": 100|200|300|400, "evidence": [{"criterion": "...", "quote": "Exact substring from content_markdown"}], "reasoning": "Korean rationale (<=500 chars)"}
