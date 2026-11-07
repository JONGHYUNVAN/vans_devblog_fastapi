### Objective
Write a complete Korean technical article that proves and explains `topic.core_mechanism` for `topic.audience` at `topic.target_level` depth (`level_criteria`).

### Section Goals & Content Integrity
1. **Direct Engineering Prose**: Focus purely on technical causality and code proof. Avoid filler intros, rhetorical clichés, or artificial role-play.
2. **Section Responsibilities**:
   - `intro`: Identify the concrete technical problem or failure condition that requires `topic.core_mechanism`, and define what this article covers. Open with that problem itself, not with a general observation or a preview of the article.
   - `body-1`, `body-2`, …: Execute `topic.outline` with continuous numbering. Connect claims directly to code execution and specifications. Never silently drop edge cases or constraints when moving from "before" code to "after" code.
   - `tradeoff` (if in `required_sections`): Specify the exact technical costs/overhead of this mechanism, state the assumptions (`topic.out_of_scope`), and give clear criteria for when to use or avoid it. When `tradeoff` is not required, state the assumptions in the final section. The final section is criteria and assumptions, never a recap of the body.
3. **Reader & Evidence Rules**:
   - Assume `topic.prerequisites` without redundant basic explanations; explain all new concepts/APIs clearly.
   - Do not implement `topic.out_of_scope` in code.
   - If `evidence` is empty, never fabricate production outages or benchmark numbers.

### Length Budget & Korean Prose
- **Budget**: `max_chars` is a ceiling, not a target—aim for about 60–70% of it in total (anchors and code included, above `min_chars`) so reviewers have room to add text. Spend length on the depth of `topic.core_mechanism`, never on adjacent topics, alternative approaches, or background `topic.audience` already has.
- **Code Share**: Code fences take at most about 40% of the total characters, and only code the prose explains. A complete `python`/`typescript` file is justified only for the module the article is about or for the single script that proves the central claim; tests, fixtures, harnesses, and everything else are `-snippet` excerpts trimmed to the lines the prose refers to.
- **Say It Once**: Each proposition appears once, in the section where it belongs—not in `intro`, again around the code, and again in the final section.
- **Register**: Plain written Korean ending in "~다/~한다/~이다" (해라체) throughout. Never "~합니다/~입니다", never imperatives ("~하십시오/~하세요"), no hedging endings ("~라고 볼 수 있다", "~인 상황이다").
- **Prose over Bullets**: Causal explanation goes in paragraphs; a list is only for ordered steps or options compared on one axis. `**bold**` only for the term a paragraph defines. One new concept per sentence, one idea per paragraph.
- **Terminology**: Use the term Korean developer writing already uses when one exists (무상태, 갱신 손실, 멱등성); otherwise keep the English original in Latin letters (Read View, HMAC). Never coin a transliteration such as "스테이트리스" or "로스트 업데이트". Give the English original in parentheses on first use: 무상태(stateless). If `topic` itself uses a coined transliteration, use the established term throughout, including `title`.
- **No Clichés**: No generic openers ("최근 많은 개발자들이…", "이 글에서는 …를 살펴본다"), no closing recap ("마치며", "정리하면", "결론적으로", "지금까지 …를 살펴봤다"), and no heading titled "들어가며", "마치며", "결론", or "요약". A heading names the specific problem or mechanism the section handles.

### Mandatory Formatting Contract (`structure_rules`)
- **Section Anchors**: Every section MUST begin with `<!-- sec:{id} -->` in order: `intro` → `body-1`, `body-2`, … → remaining `required_sections`.
- **Code Anchors & Tags**: Minimum `min_code_blocks` blocks. Put `<!-- sec:code-N -->` (N=1, 2, …) on the line immediately above every code fence.
  - Use `python`, `typescript`, or `json` ONLY for complete files that pass strict syntax parsing.
  - Use `python-snippet` or `typescript-snippet` for partial code excerpts.
- **Length & Syntax**: Total length of `content_markdown` must be `min_chars`..`max_chars`. Never use anything in `forbidden_syntax` (HTML tags other than section anchors, images, footnotes, mermaid).

### Output Schema
Output ONLY the raw JSON object (no ```json fences):
{"title": "Korean title (<=100 chars)", "content_markdown": "Full Korean markdown body", "summary": "Korean meta description (<=150 chars)"}
