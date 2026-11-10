# Task: Write Technical Article Draft (Korean)

## Step 1: Audience & Depth Alignment
1. Target Reader: Write for `topic.audience`, assuming prior knowledge of `topic.prerequisites`. Do not spend paragraphs defining prerequisite concepts, but clearly explain any new API, spec, or mechanism introduced beyond `topic.prerequisites`.
2. Target Depth: Fulfill the `assessment` and `evidence` criteria for `topic.target_level` in `level_criteria`.
3. Factual Integrity: Use only facts from `evidence` for firsthand operational claims. When `evidence` is empty, never invent production incidents or benchmark figures. Rely strictly on verifiable specifications and code behavior.

## Step 2: Section Purpose & Prose Density Execution
Write the article in Korean so each section achieves its purpose for the reader:
1. `intro`: Establish the concrete technical limitation or question that motivates the topic and declare `topic.core_mechanism` as the focus of the article. Open with that limitation itself, not with a general observation about the industry or a preview of the article.
2. `body-1`, `body-2`, …: Progress through `topic.outline` to explain both how `topic.core_mechanism` operates and why each design or implementation choice is required.
   - **Prose Density Guardrail**: Do NOT compress core causal explanations into superficial bullet-point summaries. Explain execution flow, state changes, and causal links between the code and system behavior in cohesive prose paragraphs. This guardrail never overrides `max_chars`: depth comes from explaining the single mechanism, not from adding adjacent material.
   - **Constraint Continuity**: Any difficult condition or edge case present in "before" code must either be handled in the "after" code or explicitly addressed as out of scope—never drop it silently.
3. `tradeoff` (if in `required_sections`): Define concrete adoption criteria, system overhead/costs introduced by this mechanism, and the boundary assumptions (`topic.out_of_scope`). Do not implement `topic.out_of_scope` in code. When `tradeoff` is not required, state the boundary assumptions in the final section. The final section is criteria and boundaries, never a recap of the body.

## Step 3: Length Budget & Korean Prose
1. **Length Budget**: `max_chars` is a ceiling, not a target. Aim for about 60–70% of `max_chars` in total (anchors and code included, above `min_chars`); reviewers add text in revision, so leave that headroom. Spend length on the depth of `topic.core_mechanism`, never on adjacent topics, alternative approaches, or background `topic.audience` already has.
2. **Code Share**: Code fences take at most about 40% of the total characters, and only code the prose explains. Write a complete `python`/`typescript` file only for the module the article is about or for the single script that proves the central claim; tests, fixtures, harnesses, and everything else are `-snippet` excerpts trimmed to the lines the prose refers to.
3. **Say It Once**: State each proposition once, in the section where it belongs—not in `intro`, again around the code, and again in the final section.
4. **Code Comments**: Every code block of a dozen lines or more carries Korean comments where it matters—why this branch exists, which invariant a line protects, what a reader must not change. A file-path header alone does not count; put at least one comment every 20–25 lines. The validation gate rejects long blocks without comments. State requirements plainly, without hedging endings ("~라고 볼 수 있다", "~인 상황이다").
5. **Sentence Discipline**: One new concept per sentence and one idea per paragraph. Say why something is needed before listing identifiers or API names. Use `**bold**` only for the term a paragraph defines.
6. **Terminology**: Use the term Korean developer writing already uses when one exists (무상태, 갱신 손실, 멱등성); otherwise keep the English original in Latin letters (Read View, HMAC). Never coin a transliteration such as "스테이트리스" or "로스트 업데이트". Give the English original in parentheses on first use in the body (never in `title`): 무상태(stateless). If `topic` itself uses a coined transliteration, use the established term throughout, including `title`.
7. **No Clichés**: No generic openers ("최근 많은 개발자들이…", "이 글에서는 …를 살펴본다"), no closing recap ("마치며", "정리하면", "결론적으로", "지금까지 …를 살펴봤다"), and no heading titled "들어가며", "마치며", "결론", or "요약". A heading names the specific problem or mechanism the section handles.
8. **Title**: `title` is a blog headline for the finished article, not `topic.title` (a scope statement written for the pipeline; never copy it). Choose the one form that fits what the article mainly does: a symptom the reader runs into → "…하는 이유" or "왜 …할까?"; a problem fixed → "…에서 … 문제 해결하기" or "… 없애기"; a technique applied → "…로 …하기"; how something works → "…는 어떻게 동작할까" or "… 이해하기". In every form: at most 40 characters, one promise rather than a list of everything the article covers, the words a reader would search for, no parentheses or English glosses, no colon subtitle, and no first-person experience framing ("도입기", "개선기").

## Step 4: Structural & Syntax Validation (`structure_rules`)
1. Section Anchors: Place `<!-- sec:{id} -->` at the start of every section in the exact order: `intro` → `body-1`, `body-2`, … → remaining `required_sections`.
2. Code Blocks: Include at least `min_code_blocks` blocks. Place `<!-- sec:code-N -->` (N = 1, 2, …) on the line right above each code fence.
   - Tag complete, AST-parseable modules/files as `python`, `typescript`, or `json`.
   - Tag partial excerpts or method snippets as `python-snippet` or `typescript-snippet`.
3. Length & Syntax: Keep total `content_markdown` length between `min_chars` and `max_chars`. Never use anything in `forbidden_syntax` (non-anchor HTML tags, images, footnotes, mermaid code blocks).

## Output Format
Return ONLY a single raw JSON object (no markdown fences, no preamble):
{"title": "Korean title (<=40 chars)", "content_markdown": "Full Korean markdown body", "summary": "Korean meta description (<=150 chars)"}
