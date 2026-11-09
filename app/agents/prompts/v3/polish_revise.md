<task>
You wrote this Korean technical blog post. Another model copy-edited it for AI-style wording and proposed paragraph rewrites. The input JSON has your numbered `paragraphs` (`index`, `text`), the editor's `proposals` (`index`, `text`: a proposed replacement for that paragraph, not applied yet), and `findings`: phrases that code matched against AI-style rules (`index`, `rule`, `label`, `match`). Review each proposal and make the final edit yourself. The published post must stay your own writing.
</task>

<rules>
- For each proposal, compare it with your paragraph. Accept it as is, rewrite the paragraph in your own words to fix the same problem, or reject it and keep your paragraph.
- Reject a proposal that changes a fact, a claim, its strength, or what the paragraph means, or that reads less naturally than your original.
- Edit only paragraphs that have a proposal.
- Keep the polite register of the original paragraph (~습니다, ~입니다); do not switch to ~다 or ~요.
- Do not add content, metaphors, emphasis, qualifiers, or summary phrases that were not in the original.
</rules>

<validator_constraints>
A patch that violates any of these is discarded automatically:
1. Keep every inline code span (`...`), number, URL, Latin-script word, and section anchor (`<!-- sec:id -->`) character-for-character and in the same order.
2. Keep the count of the negation morphemes 않, 못, 없, 아니, 아닌 and 아닙 in each paragraph. Only the contrastive connective 아니라 is exempt.
3. The paragraph length may change by at most 25% (30 characters for short paragraphs).
4. A patch is one paragraph: no headings, lists, or code blocks, and the same number of bold, italic, and link marks.
</validator_constraints>

<output_contract>
Output ONLY a single valid JSON object without explanations or markdown code fences. Include only the paragraphs you change; a rejected proposal gets no patch. If you reject every proposal, return `"patches": []`.
{"patches": [{"index": 0, "text": "Final Korean paragraph"}]}
</output_contract>
