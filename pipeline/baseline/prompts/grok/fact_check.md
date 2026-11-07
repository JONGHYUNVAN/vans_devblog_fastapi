### Objective
Verify every objective factual claim in `content_markdown` with web search against official documentation, specifications, RFCs, and release notes (English queries first). Memory is not evidence. Also report structural defects separately.

### Scope
- `scope == "FULL"`: claims from the whole body. `scope == "SPOT_CHECK"`: new claims only from `changed_section_ids` (none if empty).
- Skip anything in `known_claims`.
- For each entry in `my_previous_items`: decide resolved or not from `resolutions` and the current text, with the reason.

### Claims
Extract only what documentation can confirm or refute: `API`, `CONFIG_OPTION`, `VERSION`, `DB_SYNTAX`, `PROTOCOL_OR_FIGURE`. Never extract design rationale, explanations of the article's own example code, opinions, or recommendations.
- `SUPPORTED`: official/primary source confirms it. Exact URL required.
- `REFUTED`: official source contradicts it. Exact URL and the precise difference required.
- `UNVERIFIABLE`: no official source or inconclusive. Do not guess.
- `link_status` gives HTTP codes for the draft's URLs. A broken link proves nothing.

### Structural Review (not facts)
Report, separately from claims: preconditions/edge cases required for `topic.core_mechanism` that the body omits; incomplete tables/section structure; body and code failing to deliver what `title` and `topic.core_mechanism` promise. Never demand implementing `topic.out_of_scope`; only check that boundaries are stated. Severity: `MAJOR` for a missing essential precondition or an undelivered core promise, `MINOR` otherwise (depth below `topic.target_level` is `MINOR`). Never flag `tone_preservation.must_not_flag`. No items if none exist.

### Report (free text, Korean; a downstream converter maps it into JSON)
- Per claim: `[section_id]`, `[claim_type]`, `[exact claim text]`, `[SUPPORTED | REFUTED | UNVERIFIABLE]`, `[source_url]`, `[evidence]`.
- Per structural item: `[section_id]`, `[severity]`, `[comment]`, `[suggested_fix]`.
- Per `my_previous_items` entry: `item_id`, `resolved: true/false`, reason.
- `FULL` only: overall verdict (`REQUEST_CHANGES` if any claim is `REFUTED` or any structural item is `MAJOR`, else `APPROVE`) and a summary under 1000 Korean characters.
