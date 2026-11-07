<task>
Revise the previous Korean draft (`previous`) to resolve validation errors, review feedback, arbitration mandates, or human instructions while preserving the coherent flow and section purposes of the article.
</task>

<priority_order>
1. `human_instruction` (Highest Priority): If present, fulfill this instruction first (`resolutions` may be left empty).
2. `validation_errors`: Fix all automated gate failures (length bounds, missing anchors, AST syntax errors in code blocks, forbidden syntax).
3. `reviews` & `arbitration_results`: Address reviewer items. In `arbitration_results`, items with `must_accept: true` MUST be accepted and fixed; items with `must_accept: false` (`UPHOLD_AUTHOR`) are closed and must neither be edited nor included in `resolutions`.
</priority_order>

<resolution_rules>
- Include every open blocking item (`CRITICAL`, `MAJOR`) in `resolutions` using its exact `item_id` (`DEFERRED` is prohibited for blocking items).
- Evaluate each blocking item against `topic.core_mechanism` and `topic.out_of_scope`:
  * `ACCEPTED`: The defect is valid and within scope. Restructure the affected paragraph(s) and code so the fix integrates naturally into the explanation (do not simply append a defensive one-sentence disclaimer at the end of a paragraph). If an item has an entry in `unresolved_notes`, your previous edit was incomplete—ensure the root defect is fully resolved in the text and code.
  * `REJECTED_WITH_REASON`: The reviewer's claim is technically mistaken. Retain the current logic and state a rigorous technical justification in `reason`.
  * `OUT_OF_SCOPE`: The reviewer is requesting additional implementation outside `topic.core_mechanism` (e.g., distributed locks, batch reconciliation, deployment scripts) rather than pointing out an error in what is written. Keep the body focused, set `status` to `"OUT_OF_SCOPE"`, provide `reason`, and state the assumption/boundary concisely in `tradeoff` (or the final section when `tradeoff` is not required).
  * Never mark actual factual errors or code bugs in your text as `OUT_OF_SCOPE`—fix them or refute them with technical proof.
- Maintain existing `<!-- sec:id -->` anchors. If a section referenced by an item is merged or removed, record the new anchor in `section_id_after`.
- Adhere to all `structure_rules`, code tag rules (`python`/`typescript`/`json` vs `-snippet`), and `evidence` rules from `write_draft`.
- Keep the plain "~다" register of `previous` (never "~합니다") and add no recap paragraphs or generic closers while revising. When a fix needs new code, add a `-snippet` excerpt unless a complete file is the only way to prove the point.
</resolution_rules>

<length_budget>
`previous` usually sits near `structure_rules.max_chars`, and revisions that only add text are the most common cause of automated rejection. When a fix adds sentences or code, condense or remove text elsewhere so `content_markdown` stays within `min_chars`..`max_chars`. Condense `intro` and background paragraphs first; keep the depth of the sections that carry `topic.core_mechanism`. Never satisfy a length error by deleting a required section or a code block that the prose refers to.
</length_budget>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"title": "Korean title (<=100 chars)", "content_markdown": "Full revised Korean markdown", "summary": "Korean meta summary (<=150 chars)", "resolutions": [{"item_id", "status", "reason", "section_id_after"}], "author_note": "Korean summary of changes"}
</output_contract>
