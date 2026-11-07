<task>
Propose 3 distinct technical article topics in priority order based on the input JSON.
Target Search Domain: $writer_bias
</task>

<topic_design_goals>
Each proposal must be designed so that its target reader gains a clear, actionable mental model of a single engineering mechanism:
- `core_mechanism`: Define in one sentence the single mechanism or design decision the article will explain and prove from start to finish. Do not combine separate mechanisms into one topic. The `title` must directly reflect this mechanism.
- `out_of_scope`: Specify at least one adjacent topic that readers might expect but the article will intentionally exclude to preserve depth.
- `audience`: Describe the specific developer profile and the concrete technical problem or limitation they are facing.
- `outline`: Provide 3 or more logical stages showing how the article will build its proof for the reader.
- `rationale`: Explain why this topic delivers distinct engineering value when analyzed through the Target Search Domain.
</topic_design_goals>

<constraints>
- Topic Filtering: If `theme_keyword` is present, align topics with that keyword; otherwise explore the Target Search Domain. Never overlap with `avoid_topics` in title, keywords, or core mechanism. If `conflict_feedback` is provided, pivot away from the flagged overlap.
- Category & Keywords: Select an exact `(main_category_id, sub_category_id)` pair from `categories`. Provide exactly 3 `keywords` (lowercase English or Korean nouns).
- Level & Evidence Rules:
  * Choose `target_level` from `level_criteria.criteria` (`100`, `200`, `300`, `400`) based on causal depth rather than breadth.
  * State assumed prior knowledge in `prerequisites` (or `"없음"` if none).
  * Select `400` ONLY when input `evidence` is provided. If `persona.persona_id` is `troubleshooter` and `evidence` is absent, select up to `200`.
  * If `evidence` is provided, ground the topic in those records. If `evidence` is absent, never propose topics that presuppose firsthand production outages or private benchmark data (analyzing public postmortems, specs, or reproducible code behavior is allowed).
- Language: Write all string fields (`title`, `audience`, `outline`, `rationale`, `core_mechanism`, `out_of_scope`, `prerequisites`) in Korean.
- Terminology: Use the term Korean developer writing already uses when one exists (무상태, 갱신 손실, 멱등성); otherwise keep the English original in Latin letters (Read View, HMAC). Never coin a transliteration such as "스테이트리스" or "로스트 업데이트". In `title` and `core_mechanism`, give the English original in parentheses on first use: 무상태(stateless). The writer follows the topic's wording, so a wrong term here spreads through the whole article.
</constraints>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"proposals": [{"title", "audience", "keywords", "outline", "rationale", "core_mechanism", "out_of_scope": [], "target_level", "prerequisites", "main_category_id", "sub_category_id"} × 3]}
</output_contract>
