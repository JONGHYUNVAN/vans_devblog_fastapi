### Objective
Generate 3 high-signal technical article topic proposals in priority order based on the input JSON.
Search Domain: $writer_bias

### Hard Rules
1. **Focus & Deduplication**:
   - Align with `theme_keyword` if present; otherwise stay strictly within the Search Domain.
   - Zero overlap with `avoid_topics` or issues raised in `conflict_feedback`.
   - Use only exact `(main_category_id, sub_category_id)` pairs from `categories`.
   - `keywords`: Exactly 3 lowercase English or Korean nouns.
2. **Single Mechanism & Falsifiable Scope**:
   - `core_mechanism`: One sentence defining the exact technical mechanism or failure mode to be proven in code. Never mix two separate mechanisms in one proposal. `title` must reflect `core_mechanism`.
   - `out_of_scope`: At least 1 adjacent concern explicitly excluded from implementation.
   - `audience`: The specific engineering problem or misconception the target reader faces.
   - `outline`: At least 3 sequential stages of technical proof.
3. **Level & Evidence Gate**:
   - Select `target_level` from `level_criteria.criteria` (`100`..`400`).
   - If `evidence` is absent: Never propose topics claiming firsthand production outages or private benchmarks (public specs, reproducible edge cases, and public postmortems are valid). Level `400` is forbidden without `evidence`. If `persona.persona_id == "troubleshooter"` and `evidence` is absent, `target_level` must be `100` or `200`.
4. **Language**: All text values must be written in Korean.
5. **Terminology**: Use the term Korean developer writing already uses when one exists (무상태, 갱신 손실, 멱등성); otherwise keep the English original in Latin letters (Read View, HMAC). Never coin a transliteration such as "스테이트리스" or "로스트 업데이트". In `title` and `core_mechanism`, give the English original in parentheses on first use: 무상태(stateless). The writer follows the topic's wording, so a wrong term here spreads through the whole article.

### Output Schema
Output ONLY the raw JSON object (no ```json fences):
{"proposals": [{"title", "audience", "keywords", "outline", "rationale", "core_mechanism", "out_of_scope": [], "target_level", "prerequisites", "main_category_id", "sub_category_id"} × 3]}
