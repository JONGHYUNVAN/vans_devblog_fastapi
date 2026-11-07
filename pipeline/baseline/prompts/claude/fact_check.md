<task>
Verify the objective factual claims in `content_markdown` against official documentation, specifications, RFCs, and release notes using web search (English queries first for official sources). Never judge from memory. Separately, report structural defects of the article.
</task>

<scope_rules>
- `scope == "FULL"`: extract claims from the whole `content_markdown`.
- `scope == "SPOT_CHECK"`: extract new claims only from the sections listed in `changed_section_ids` (extract none if the list is empty).
- Skip any claim already listed in `known_claims`.
- If `my_previous_items` is non-empty, judge each `item_id` against the author's `resolutions` and the current text: resolved or not, and why.
</scope_rules>

<claim_extraction>
Extract only claims that external documentation can confirm or refute:
- `API`: framework/library method behavior, signatures, return values, exceptions.
- `CONFIG_OPTION`: configuration key names, default values, activation conditions.
- `VERSION`: version-specific introductions, deprecations, or behavioral changes.
- `DB_SYNTAX`: database query syntax, locking behavior, isolation semantics, execution plan rules.
- `PROTOCOL_OR_FIGURE`: protocol specifications, delivery guarantees, official specification numbers.
Do not extract design rationale, causal explanations of the article's own example code, opinions, or recommendations.
</claim_extraction>

<verdicts>
- `SUPPORTED`: confirmed by an official or primary source. Record the exact URL.
- `REFUTED`: the source contradicts the draft. Record the exact URL and state precisely how the source differs from the draft.
- `UNVERIFIABLE`: no official source found or inconclusive. Never guess.
- `link_status` lists HTTP status codes for URLs in the draft. A broken link is never supporting evidence.
</verdicts>

<structural_review>
This is not fact checking. Separately report structural defects: preconditions or edge cases required for `topic.core_mechanism` to work that the body omits; incomplete tables or section structure; the body and code not delivering what `title` and `topic.core_mechanism` promise. Never demand implementing anything in `topic.out_of_scope`; only check that assumptions and boundaries are stated. Give each structural item a severity: `MAJOR` when an essential precondition is missing or the core promise is not delivered, `MINOR` otherwise. Depth below `topic.target_level` is `MINOR`. Never flag `tone_preservation.must_not_flag`. Do not invent items; report none if none exist.
</structural_review>

<report_format>
Write a free-text report in Korean so the downstream converter can map every field into JSON:
- For every claim: `[section_id]`, `[claim_type]`, `[exact claim text]`, `[SUPPORTED | REFUTED | UNVERIFIABLE]`, `[source_url]`, `[evidence]`.
- For every structural item: `[section_id]`, `[severity]`, `[comment]`, `[suggested_fix]`.
- If `my_previous_items` was given: each `item_id`, `resolved: true/false`, and the reason.
- If `scope == "FULL"`: end with an overall verdict (`APPROVE` or `REQUEST_CHANGES`; `REQUEST_CHANGES` whenever any claim is `REFUTED` or any structural item is `MAJOR`) and a summary under 1000 Korean characters.
</report_format>
