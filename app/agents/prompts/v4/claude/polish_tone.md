<task>
Inspect the numbered Korean `paragraphs` (`index`, `text`) in the input JSON and refine ONLY sentences suffering from severe translationese or broken grammatical agreement that obstruct reading.
</task>

<editing_rules>
- Target Defects Only: Fix awkward English-to-Korean translationese (e.g., broken double-subject structures like "동시 중복 요청은 기본 키 제약이 두 번째 insert를 막는다" or unnatural double passives like "~에 의해 처리되어진다").
- Allow Technical Subjects: System components, threads, queries, or modules acting as the grammatical subject are natural in Korean engineering prose—do not rewrite inanimate subjects if the sentence reads clearly.
- No Mechanical Splitting: Do not split paragraphs or chop up sentences unless a sentence is genuinely ungrammatical.
- Leave clean paragraphs untouched. If no paragraph needs editing, return `"patches": []`.
- Hard Validator Constraints (violating any constraint causes the patch to be rejected):
  1. Never add new explanations or delete existing technical meaning.
  2. Preserve every inline code span (`` `...` ``), number, URL, and section anchor (`<!-- sec:id -->`) character-for-character.
  3. Never change the exact count of Korean negation morphemes (`않`, `못`, `없`) in the paragraph.
</editing_rules>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"patches": [{"index": 0, "text": "Edited Korean paragraph"}]}
</output_contract>
