# Task: Propose 3 Technical Article Topics
Target Search Domain: $writer_bias

## Step 1: Domain Exploration & Deduplication
1. If `theme_keyword` is specified in the input JSON, anchor all 3 topics to that keyword. Otherwise, select topics within the Target Search Domain above.
2. Cross-check against `avoid_topics` and `conflict_feedback`. Exclude any topic that overlaps in title, keywords, or underlying mechanism.
3. Map each proposal to an exact `(main_category_id, sub_category_id)` pair from the input `categories` list. Never invent category IDs.
4. Set `keywords` to an array of exactly 3 nouns (lowercase English or Korean).

## Step 2: Scope, Audience, and Depth Definition
For each of the 3 proposals, define all fields in Korean:
1. `core_mechanism`: State in a single sentence the one technical mechanism or decision the article will dissect from start to finish. Ensure `title` promises this exact mechanism.
2. `out_of_scope`: List 1 or more related topics intentionally excluded so the article remains focused.
3. `audience` & `prerequisites`: Specify the target developer's concrete problem context in `audience`, and required prior knowledge in `prerequisites` (`"없음"` if none).
4. `target_level`: Pick `100`, `200`, `300`, or `400` from `level_criteria.criteria` based on how deeply a single decision's causality is explained.
   - Level `400` is allowed ONLY if input `evidence` is non-empty.
   - If `persona.persona_id` is `troubleshooter` and `evidence` is empty, maximum level is `200`.
   - Without `evidence`, never propose topics requiring private production incident logs or internal benchmark measurements.
5. `outline` & `rationale`: Construct a step-by-step `outline` (minimum 3 items) and explain the topic's engineering value in `rationale`.
6. Terminology: Use the term Korean developer writing already uses when one exists (무상태, 갱신 손실, 멱등성); otherwise keep the English original in Latin letters (Read View, HMAC). Never coin a transliteration such as "스테이트리스" or "로스트 업데이트". In `title` and `core_mechanism`, give the English original in parentheses on first use: 무상태(stateless). The writer follows the topic's wording, so a wrong term here spreads through the whole article.

## Output Format
Return ONLY a raw JSON object matching the schema below (no markdown fences, no commentary):
{"proposals": [{"title", "audience", "keywords", "outline", "rationale", "core_mechanism", "out_of_scope": [], "target_level", "prerequisites", "main_category_id", "sub_category_id"} × 3]}
