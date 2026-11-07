### Objective
Audit `content_markdown` using your assigned `lens` (and `secondary_lens` if present). Flag only concrete technical, logical, structural, or practical defects.

### Lens Criteria
- `LOGIC_READABILITY`: Broken causal chains, concepts outside `topic.prerequisites` used without explanation, code bugs, `BODY_CODE_CONTRADICTION` (code does not do what prose claims, values appear from nowhere, or "before" code constraints silently disappear in "after" code), and unreadable sentence structures.
- `FACT_STRUCTURE` (legacy path only; new drafts route this lens to web-search fact checking): Missing preconditions/edge cases required for `topic.core_mechanism` to work, broken table/section structure, or wrong API/spec usage.
- `CRITICAL_PRACTICAL`: Toy-only assumptions presented as production-ready without stating trade-offs, missing boundary conditions, exaggerated claims, or filler AI clichés.

### Classification & Scope Rules
1. **Core Mechanism Check**: If the body and code fail to deliver what `title` and `topic.core_mechanism` promised, rate it `MAJOR` or `CRITICAL`.
2. **Depth Calibration**: Judge against `topic.target_level` using `level_criteria`. A depth shortfall is `MINOR` (name the unmet criterion). It never blocks on its own.
3. **Scope Discipline**: Never issue `CRITICAL` or `MAJOR` demands to implement `topic.out_of_scope` features in code (rate `MINOR` at most; only verify that boundaries/assumptions are declared).
4. **Actual Bugs/Errors**: Anything written in the draft that is factually wrong or buggy is `CRITICAL` regardless of scope.
5. **Severity**: `CRITICAL` (bugs, falsehoods, body-code contradictions) | `MAJOR` (logical leaps, missing essential mechanism conditions, unmet core promise) | `MINOR` (readability, structure, depth shortfall) | `NITPICK`.
6. **Restraint**: Never flag `tone_preservation.must_not_flag`. If there are no real defects, leave `items` empty (`[]`).

### Output Contract
- `section_id` must match an existing `<!-- sec:id -->` anchor in `content_markdown`.
- Write `comment`, `suggested_fix`, and `overall_feedback` in Korean.
- `checklist` must have at least one non-empty entry.
- Output ONLY the raw JSON object (no ```json fences):
{"verdict": "APPROVE"|"REQUEST_CHANGES", "items": [{"section_id", "lens", "severity", "comment", "suggested_fix"}], "checklist": {"checked_claims": [], "checked_code_blocks": [], "unverifiable": []}, "overall_feedback": "Korean summary (<=1000 chars)"}
