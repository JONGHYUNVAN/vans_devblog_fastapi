# Task: Full Technical Review
Evaluate `content_markdown` strictly through your assigned `lens` (and `secondary_lens` if provided).

## Step 1: Lens-Specific Inspection
Inspect the draft according to your assigned lens(es):
- `LOGIC_READABILITY`: Check for logical leaps, unexplained concepts outside `topic.prerequisites`, code bugs, `BODY_CODE_CONTRADICTION` (mismatch between prose explanation and actual code, unexplained variables, or constraints in "before" code silently dropped in "after" code), and hard-to-parse sentences.
- `FACT_STRUCTURE` (legacy path only; new drafts route this lens to web-search fact checking): Check for missing preconditions/edge cases required for `topic.core_mechanism` to function, incomplete tables/structure, and wrong API signatures or spec assumptions.
- `CRITICAL_PRACTICAL`: Check for unaddressed practical counterarguments, missing assumptions/trade-offs (`tradeoff`), exaggerated claims, and hollow AI clichés.

## Step 2: Scope & Severity Filtering
1. **Core Promise Verification (All Lenses)**: If the article and code fail to actually show what `title` and `topic.core_mechanism` promised, log a `MAJOR` or `CRITICAL` item.
2. **Depth Calibration (All Lenses)**: Use `topic.target_level` and `level_criteria` to judge what the reader needs. A shortfall against the `assessment`/`evidence` standard of `topic.target_level` is logged as `MINOR` with the unmet criterion named. It never blocks on its own.
3. **Out-of-Scope Protection**: Do NOT raise `CRITICAL` or `MAJOR` items demanding code implementation of `topic.out_of_scope` topics (e.g., multi-instance locking, batch reconciliation, deployment migration). Only check that assumptions/boundaries are stated (rate implementation demands `MINOR` at most).
4. **Zero Tolerance for Errors**: Any factual error or code bug actually written in the text is `CRITICAL` regardless of scope.
5. **Tone Rules**: Never flag anything in `tone_preservation.must_not_flag`. Do not invent forced feedback—if no defects exist, return `"items": []`.

## Step 3: Build Review Output
- Every item must cite an existing `section_id` from the draft's `<!-- sec:id -->` anchors and use your assigned `lens` or `secondary_lens`.
- Write `comment`, `suggested_fix`, and `overall_feedback` in Korean.
- Record at least one entry in `checklist` (`checked_claims`, `checked_code_blocks`, or `unverifiable`).
- If any `CRITICAL` or `MAJOR` item exists, set `verdict` to `"REQUEST_CHANGES"`; otherwise `"APPROVE"`.

## Output Format
Return ONLY a single raw JSON object (no markdown fences):
{"verdict": "APPROVE"|"REQUEST_CHANGES", "items": [{"section_id", "lens", "severity", "comment", "suggested_fix"}], "checklist": {"checked_claims": [], "checked_code_blocks": [], "unverifiable": []}, "overall_feedback": "Korean summary (<=1000 chars)"}
