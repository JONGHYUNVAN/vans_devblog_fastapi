<task>
Convert `verification_text` and `citations` from the input JSON into the required output JSON schema without adding, omitting, or reinterpreting any judgment.
</task>

<mapping_rules>
- Map every claim in `verification_text` to `claims`:
  * `section_id`: Must be a valid ID from the input `section_ids` list.
  * `claim_type` and `status`: Must match the schema enum values.
  * `source_url`: Must be an exact URL present in the input `citations` list; if absent from `citations`, set to `null`.
- Map ONLY the structural items (not claims) to `items` (when `scope` is `FULL`) or `new_critical_items` (when `scope` is `SPOT_CHECK`, `CRITICAL` only). Set `lens` to the input `lens`. Copy `severity` as written; if unstated, use `MINOR`. Never copy a claim into `items`—the pipeline derives claim severity itself.
- When `scope` is `SPOT_CHECK`, map every ID in `expected_item_ids` to `item_checks` (`note` is required whenever `resolved` is `false`).
- For `FULL`, copy `verdict` (default `"APPROVE"` if unstated) and `overall_feedback` (max 1000 chars, default `""` if unstated).
- Populate `checklist` (`checked_claims` for verified claims, `unverifiable` for `UNVERIFIABLE` claims).
</mapping_rules>

<output_contract>
Output ONLY a single valid JSON object matching the schema without explanations or markdown code fences.
</output_contract>
