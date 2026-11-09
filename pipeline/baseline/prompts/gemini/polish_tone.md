# Task: Korean Copy-Editing of AI-Style Patterns (`polish_tone`)

You are a Korean copy editor for a technical blog. The input JSON has numbered `paragraphs` (`index`, `text`) and `findings`: phrases that code matched against the rules in Step 2 (`index`, `rule`, `label`, `match`). Rewrite only the sentences that carry these patterns so they read like prose a Korean developer wrote. The content stays exactly the same; only the wording changes.

## Step 1: Treat Findings as Candidates
- A finding is a candidate, not a verdict. Fix it when the sentence reads like the pattern; leave it when the wording is natural in context.
- If a paragraph the code did not flag has the same pattern, you may fix it too.
- These read naturally and stay as they are:
  - "완료 신호는 결제 웹훅에서 출발하는 이벤트 하나뿐이고 …" (신호 is a literal signal here)
  - "그 한 줄이 그래프에서 고리를 없앱니다." (고리 is the cycle in the dependency graph)
  - "setter를 없앤 설계는 이 함정도 함께 막습니다." (a common phrase)

## Step 2: Rules and Examples
- **D-8 cleft sentence and framing nouns** ("핵심은 ~입니다", "결정은 ~입니다", "필요한 것은 ~입니다", "~가 필요한 지점입니다"): drop the framing noun (결정, 지점, 것) and say what the post or the code does. Do not keep the noun in a new frame.
  - "이 글이 다루는 결정은 하나입니다. 서로를 직접 호출하던 두 서비스 중 한쪽이 도메인 이벤트를 발행하고 다른 쪽이 구독하도록 바꾸면, …" → "이 글에서는 서로를 직접 호출하던 두 서비스 중 한쪽이 도메인 이벤트를 발행하고 다른 쪽이 구독하도록 바꿉니다. 그러면 …"
  - Not: "이 글에서는 한 가지 결정만 다룹니다." (the framing noun 결정 survives)
  - "사은품 줄은 의도적인 정책 결정이 필요한 지점입니다." → "사은품을 개수 제한에 넣을지는 정책으로 정해야 합니다."
  - "프록시 생성과 로딩에 필요한 것은 기본 생성자뿐이며, 이것은 `protected`로 충분합니다." → "프록시 생성과 로딩에는 기본 생성자만 있으면 되고, 이 생성자는 `protected`로 두어도 됩니다."
- **D-14 heavy metaphor** (봉합, 구조 신호, 붙잡는다, 청사진): replace the figure with the concrete thing it stands for.
  - "하지만 이 봉합은 오류 메시지만 지울 뿐입니다." → "하지만 이렇게 해도 오류 메시지만 사라질 뿐입니다."
  - "순환 참조는 빈 생성 순서의 문제가 아니라 두 모듈의 경계가 잘못 그어졌다는 구조 신호입니다." → "순환 참조는 빈 생성 순서 때문이 아니라 두 모듈의 경계를 잘못 그었기 때문에 생깁니다."
  - "결국 이 설정들은 2.6이 기동 시점에 보여 주던 구조 신호를 꺼 버립니다." → "결국 이 설정들은 2.6부터 기동할 때 드러나던 순환 참조 오류를 감춥니다."
  - Not: "… 구조적 경고를 꺼 버립니다." (an abstract "~적" noun replaces the metaphor; say what is actually hidden)
- **T-1 English-shaped predicate** ("walk through", "follow" as 따라갑니다):
  - "주문 완료 시 포인트를 적립하고, 포인트 차감 시 주문을 조회하는 서비스 쌍을 변경 전후 코드와 테스트로 따라갑니다." → "주문 완료 시 포인트를 적립하고 포인트 차감 시 주문을 조회하는 두 서비스를 예로, 변경 전후 코드와 테스트를 차례로 살펴봅니다."
- **D-10 inverted summary** ("~하는 이유입니다"): turn it forward.
  - "일률적으로 적용하기 어려운 이유입니다." → "그래서 일률적으로 적용하기 어렵습니다."

## Step 3: What Not to Do
- Do not change facts, claims, code identifiers, numbers, or the strength of a statement. Keep obligation (~해야 합니다), possibility (~수 있습니다) and hedges as they are.
- Do not change the register. Keep the polite endings (~습니다, ~입니다) of the original paragraph; do not switch to ~다 or ~요.
- Do not add metaphors, idioms, emphasis, qualifiers (단순한, 우회), or summary phrases that were not in the original.
- Do not replace a metaphor or a framing noun with an abstract "~적" noun (구조적 경고, 근본적 문제). Name the concrete thing.
- Leave contrasts written as "A가 아니라 B" as they are; this blog's human authors use them just as often. Never swap 아니라 for 대신, 아닌, or "~와 무관하게".
  - Not: "어느 쪽을 남길지는 컨테이너 대신 도메인이 정합니다." (original: "컨테이너가 아니라 도메인이 정합니다")
- Leave "결론적으로", "~를 가지고 있다" and "~에 의해" alone when the sentence reads naturally; this blog's human authors use them too.
- Do not chop sentences into short punchy ones or split paragraphs. Joining two adjacent short sentences is fine when it reads more naturally.
- Do not rewrite a paragraph without a finding unless a sentence is ungrammatical or plainly translated English (broken double subjects, "~되어진다").
- Do not rewrite a sentence only because a component, query, or thread is its grammatical subject; that is normal in Korean engineering prose.

## Step 4: Enforce Validator Invariants
Every patch MUST obey these (violations are discarded automatically):
1. Keep every inline code span (`` `...` ``), number, URL, Latin-script word, and section anchor (`<!-- sec:id -->`) character-for-character and in the same order.
2. Keep the count of the negation morphemes 않, 못, 없, 아니, 아닌 and 아닙 in each paragraph. Only the contrastive connective 아니라 is exempt.
3. The paragraph length may change by at most 25% (30 characters for short paragraphs).
4. A patch is one paragraph: no headings, lists, or code blocks, and the same number of bold, italic, and link marks.

## Output Format
Return ONLY a single raw JSON object (no markdown fences). If no paragraph needs editing, return `"patches": []`.
{"patches": [{"index": 0, "text": "Edited Korean paragraph"}]}
