<task>
Write a complete Korean technical article based on `topic` in the input JSON.
Your objective is to enable `topic.audience` to fully understand `topic.core_mechanism` at the depth and rigor defined for `topic.target_level` in `level_criteria`.
</task>

<section_goals>
Compose sentences and organize paragraphs autonomously so that each section delivers its intended value to the reader:
- `intro`: Make it immediately clear why this technical problem or design decision matters, what concrete limitation arises without it, and what exact mechanism (`topic.core_mechanism`) this article examines. Open with the concrete limitation itself, not with a general observation about the industry or a preview of the article.
- `body-1`, `body-2`, …: Follow `topic.outline` to demonstrate how `topic.core_mechanism` works and why it is constructed that way, fulfilling the `assessment` and `evidence` standards of `topic.target_level`. Depth comes from explaining this one mechanism, not from adding adjacent material. When contrasting "before" and "after" code, never allow a constraint or edge case present in the "before" code to silently vanish in the "after" code—either resolve it in the code or explicitly state how it is scoped.
- `tradeoff` (when included in `required_sections`): Provide concrete engineering criteria so the reader can judge when to apply or avoid this mechanism, including the operational/system costs it introduces and the boundary assumptions (`topic.out_of_scope`). When `tradeoff` is not required, state the boundary assumptions in the final section. The final section is criteria and boundaries, never a recap of the body.
</section_goals>

<integrity_rules>
- Reader Calibration: Assume the reader already knows `topic.prerequisites` (skip basic definitions of prerequisite concepts), but explain any new concept, API, or internal behavior clearly in context.
- Scope & Honesty: Do not implement items listed in `topic.out_of_scope`. If `evidence` is provided, use only its facts as firsthand experience; if absent, never fabricate outages, operational history, or benchmark numbers. Only state verifiable specs, versions, and APIs.
</integrity_rules>

<length_budget>
- `max_chars` is a ceiling, not a target. Aim for about 60–70% of `max_chars` in total (anchors and code included, and above `min_chars`). Reviewers add text during revision, so leave that headroom.
- Spend length on the depth of `topic.core_mechanism`, never on adjacent topics, alternative approaches, or background that `topic.audience` already has.
- Code fences take at most about 40% of the total characters, and only code the prose actually explains. Write a complete `python`/`typescript` file only for the module the article is about, or for the single script that proves the central claim. Tests, fixtures, harnesses, and everything else are `-snippet` excerpts trimmed to the lines the prose refers to.
- State each proposition once, in the section where it belongs. Do not restate it in `intro`, again before and after the code, and again in the final section.
</length_budget>

<korean_prose>
- Code comments: every code block of a dozen lines or more carries Korean comments where it matters—why this branch exists, which invariant a line protects, what a reader must not change. A file-path header alone does not count; put at least one comment every 20–25 lines. The validation gate rejects long blocks without comments.
- Assertion: state requirements and causes plainly; do not soften them with hedging endings such as "~라고 볼 수 있다" or "~인 상황이다".
- Sentences and paragraphs: one new concept per sentence, one idea per paragraph. Say why something is needed before listing identifiers or API names.
- Paragraphs over bullets: causal explanation (what happens, why, in which order) goes in paragraphs. A list is only for a genuinely enumerable set—ordered steps, or options compared on the same axis—never for a chain of sentences that argue a point. Use `**bold**` only for the term a paragraph defines.
- Terminology: Use the term Korean developer writing already uses when one exists (무상태, 갱신 손실, 멱등성); otherwise keep the English original in Latin letters (Read View, HMAC). Never coin a transliteration such as "스테이트리스" or "로스트 업데이트". Give the English original in parentheses on first use in the body (never in `title`): 무상태(stateless). If `topic` itself uses a coined transliteration, use the established term throughout, including `title`.
- No clichés: no generic openers ("최근 많은 개발자들이…", "이 글에서는 …를 살펴본다"), no closing recap ("마치며", "정리하면", "결론적으로", "지금까지 …를 살펴봤다"), and no heading titled "들어가며", "마치며", "결론", or "요약". A heading names the specific problem or mechanism the section handles.
- Title: `title` is a blog headline for the finished article, not `topic.title` (a scope statement written for the pipeline; never copy it). Choose the one form that fits what the article mainly does: a symptom the reader runs into → "…하는 이유" or "왜 …할까?"; a problem fixed → "…에서 … 문제 해결하기" or "… 없애기"; a technique applied → "…로 …하기"; how something works → "…는 어떻게 동작할까" or "… 이해하기". In every form: at most 40 characters, one promise rather than a list of everything the article covers, the words a reader would search for, no parentheses or English glosses, no colon subtitle, and no first-person experience framing ("도입기", "개선기").
</korean_prose>

<structure_contract>
Comply strictly with `structure_rules`:
- Write in Korean (`language: "ko"`). Start every section with an anchor comment matching `structure_rules.anchor_format` (`<!-- sec:{id} -->`).
- Section order: `intro` → `body-1`, `body-2`, … (continuous numbering) → remaining `required_sections`. Every anchor in `required_sections` must be present.
- Include at least `min_code_blocks` code blocks. Place `<!-- sec:code-N -->` (N starting at 1) on the line immediately above each code fence and include a language tag:
  * Use `python`, `typescript`, or `json` ONLY for complete, self-contained files/modules that pass strict syntax/AST parsing.
  * Use `python-snippet` or `typescript-snippet` for partial excerpts or method-only fragments.
- Total character count of `content_markdown` (including anchors and code) must be within `min_chars` and `max_chars`.
- Allowed markdown: paragraphs, `#`–`######` headings, `-`/`1.` lists, `>` blockquotes, code fences, GFM tables, `---`, two-space line breaks, `**bold**`, `*italic*`, `~~strikethrough~~`, `` `code` ``, `[text](url)` links. Never use anything in `forbidden_syntax` (HTML tags other than section anchors, images, footnotes, mermaid blocks).
</structure_contract>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences:
{"title": "Korean title (<=40 chars)", "content_markdown": "Full Korean markdown body", "summary": "Korean meta description (<=150 chars)"}
</output_contract>
