### Objective
Revise the Korean draft (`previous`) to eliminate all `validation_errors`, resolve blocking `reviews`, and comply with `arbitration_results` or `human_instruction`.

### Execution Rules
1. **Priority**: `human_instruction` > `validation_errors` > `arbitration_results` & `reviews`.
2. **Arbitration Binding**: Items in `arbitration_results` with `must_accept: true` MUST be accepted and fixed in the text/code. Items with `must_accept: false` are closed—do not touch them or list them in `resolutions`.
3. **Blocking Item Resolutions (`CRITICAL`, `MAJOR`)**:
   - Every open blocking item must appear in `resolutions` with its exact `item_id` (no `DEFERRED`).
   - `ACCEPTED`: Rewrite the affected paragraph and code cleanly so the logic and code match (do not just append a lazy one-line caveat). If listed in `unresolved_notes`, fix the exact deficiency noted by the reviewer.
   - `REJECTED_WITH_REASON`: Use when the reviewer's claim is technically false; provide rigorous technical proof in `reason`.
   - `OUT_OF_SCOPE`: Use ONLY when the reviewer demands extra implementation outside `topic.core_mechanism` (never use for bugs or false statements in your draft). Declare the assumption concisely in `tradeoff` (or the last section when `tradeoff` is not required).
4. **Length Budget**: `previous` is usually near `structure_rules.max_chars`. If a fix adds text or code, cut or condense elsewhere (intro and background first) so the total stays within `min_chars`..`max_chars`. Never fix a length error by deleting a required section or a code block the prose refers to.
5. **Hard Formatting (`structure_rules`)**:
   - Preserve `<!-- sec:id -->` anchors (log any merged anchor in `section_id_after`) and `<!-- sec:code-N -->` code anchors.
   - Ensure `python`/`typescript`/`json` blocks pass strict syntax parsing (use `-snippet` for partial excerpts).
   - Stay within `min_chars`..`max_chars` and obey `forbidden_syntax` and `evidence` rules.
   - Keep the register of `previous` consistent (do not switch between 합니다 and 다 styles); add no recap paragraphs or generic closers. New code for a fix is a `-snippet` excerpt unless a complete file is the only way to prove the point, with Korean comments at the points that matter (the validation gate rejects long blocks without comments).
6. **Title**: Keep `previous.title` unless `human_instruction` or a review item concerns it, or the revision changes what the article promises. A replaced title is a blog headline of at most 40 characters with one promise, no parentheses, and no colon subtitle.

### Output Schema
Output ONLY the raw JSON object (no ```json fences):
{"title": "Korean title (<=100 chars)", "content_markdown": "Full revised Korean markdown", "summary": "Korean meta summary (<=150 chars)", "resolutions": [{"item_id", "status", "reason", "section_id_after"}], "author_note": "Korean summary of changes"}
