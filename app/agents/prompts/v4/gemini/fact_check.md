# Task: External Fact Verification via Web Search
Verify objective factual claims in `content_markdown` by searching official documentation, specifications, RFCs, and release notes (prioritize English primary sources in search queries). Never rely on internal memory. Separately, report structural defects of the article.

## Step 1: Determine Verification Scope (`scope`)
1. If `scope == "FULL"`: Extract verifiable factual claims across the entire `content_markdown`.
2. If `scope == "SPOT_CHECK"`: Extract new claims ONLY from sections listed in `changed_section_ids` (if empty, extract no new claims).
3. Skip any claim already listed in `known_claims`.
4. If `my_previous_items` is non-empty: Check each `item_id` against the author's `resolutions` and `content_markdown` to determine whether it is resolved and why.

## Step 2: Extract Objective Claims Only
Extract ONLY claims verifiable against external documentation. Do NOT extract the author's design rationale, causal explanations of custom example code, or general engineering recommendations.
Allowed claim categories:
- `API`: Framework/library method behavior, signatures, return values, exceptions.
- `CONFIG_OPTION`: Configuration key names, default values, activation conditions.
- `VERSION`: Version-specific introductions, deprecations, or behavioral changes.
- `DB_SYNTAX`: Database query syntax, locking behavior, isolation semantics, execution plan rules.
- `PROTOCOL_OR_FIGURE`: Standard protocol specifications, delivery guarantees, official specification numbers.

## Step 3: Search & Assign Verdict
Run web searches for each claim (`link_status` shows HTTP status codes for URLs in the draft; never use a broken link as supporting proof):
- `SUPPORTED`: Confirmed by official docs or primary references. Record the exact source URL.
- `REFUTED`: Contradicted by official docs. Record the exact source URL and state precisely how the official spec differs from the draft.
- `UNVERIFIABLE`: Official source not found or inconclusive. Do not guess.

## Step 4: Structural Review (not facts)
Separately from claims, report structural defects: preconditions or edge cases required for `topic.core_mechanism` to work that the body omits; incomplete tables or section structure; the body and code not delivering what `title` and `topic.core_mechanism` promise. Never demand implementing anything in `topic.out_of_scope`; only check that assumptions and boundaries are stated. Severity per item: `MAJOR` when an essential precondition is missing or the core promise is not delivered, `MINOR` otherwise. Depth below `topic.target_level` is `MINOR`. Never flag `tone_preservation.must_not_flag`. Report none if none exist.

## Step 5: Format Free-Text Report (in Korean)
Write the report in Korean so the downstream converter can map every field into JSON:
- For every claim, explicitly list: `[section_id]`, `[claim_type]`, `[exact claim text]`, `[SUPPORTED | REFUTED | UNVERIFIABLE]`, `[source_url]`, and `[verification evidence]`.
- For every structural item: `[section_id]`, `[severity]`, `[comment]`, `[suggested_fix]`.
- If `my_previous_items` was provided, list each `item_id`, its resolution status (`resolved: true/false`), and the reason.
- If `scope == "FULL"`, end with an overall verdict (`APPROVE` or `REQUEST_CHANGES` — must be `REQUEST_CHANGES` if any claim is `REFUTED` or any structural item is `MAJOR`) and an overall summary under 1000 Korean characters.
