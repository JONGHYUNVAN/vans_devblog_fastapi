# Task: Revise Technical Article Draft (Korean)

## Step 1: Triage Priorities
1. **Priority 1 (`human_instruction`)**: If present, execute the human instruction as top priority (`resolutions` may be empty).
2. **Priority 2 (`validation_errors`)**: Fix every automated check failure (character count limits, missing section/code anchors, AST syntax errors, forbidden syntax).
3. **Priority 3 (`reviews` & `arbitration_results`)**:
   - If an item has `must_accept: true` in `arbitration_results`, you MUST accept and revise it.
   - If an item has `must_accept: false` (`UPHOLD_AUTHOR`), it is closed—do not edit the text for it and do not include it in `resolutions`.

## Step 2: Classify & Execute Resolutions
Include every remaining blocking item (`CRITICAL`, `MAJOR`) in `resolutions` with its exact `item_id` (`DEFERRED` is invalid for blocking items):
1. `ACCEPTED`: For valid in-scope defects, rewrite the relevant paragraph(s) and code so the causal flow remains seamless (never tack on a one-line patch at the end of a paragraph). Check `unresolved_notes` to see why any prior revision failed and fix it completely.
2. `REJECTED_WITH_REASON`: If the reviewer is technically wrong, keep the current text/code and provide a concrete technical rebuttal in `reason`.
3. `OUT_OF_SCOPE`: If the reviewer demands implementing operational features outside `topic.core_mechanism` (and the existing text is not factually wrong), mark `"OUT_OF_SCOPE"`, explain why in `reason`, and state the boundary assumption concisely in `tradeoff` (or the final section when `tradeoff` is not required). Never use `OUT_OF_SCOPE` to excuse factual errors or buggy code in your draft.

## Step 3: Length Budget
`previous` is usually close to `structure_rules.max_chars`; revisions that only add text are the most common cause of automated rejection. Whenever a fix adds sentences or code, condense or remove text elsewhere so the total stays within `min_chars`..`max_chars`. Condense `intro` and background paragraphs first and keep the depth of the sections carrying `topic.core_mechanism`. Never fix a length error by deleting a required section or a code block the prose refers to.

## Step 4: Verify Structural Contract (`structure_rules`)
- Preserve existing `<!-- sec:id -->` anchors (if merged/renamed, set `section_id_after` to the new anchor).
- Keep all `<!-- sec:code-N -->` anchors, valid language tags (`python`/`typescript`/`json` vs `-snippet`), character limits (`min_chars`..`max_chars`), and `evidence` integrity rules.
- Keep the register of `previous` consistent (do not switch between 합니다 and 다 styles) and add no recap paragraphs or generic closers. New code for a fix is a `-snippet` excerpt unless a complete file is the only way to prove the point, with Korean comments at the points that matter (the validation gate rejects long blocks without comments).

## Output Format
Return ONLY a single raw JSON object (no markdown fences):
{"title": "Korean title (<=100 chars)", "content_markdown": "Full revised Korean markdown", "summary": "Korean meta summary (<=150 chars)", "resolutions": [{"item_id", "status", "reason", "section_id_after"}], "author_note": "Korean summary of changes"}
