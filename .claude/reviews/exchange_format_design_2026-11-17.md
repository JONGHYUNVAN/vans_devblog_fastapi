# 설명문 파이프라인 형식 설계안 (2026-11-17)

상태: A부는 **구현됨**(2026-11-17, 09 문서 D-103. 가짜 LLM 테스트만 했고 실제 실행 확인은 9절대로 남았다). B부는 **제안**이고 승인되면 D-104 로 적는다.

A부 구현이 이 문서와 다른 점: 6·7단계의 본문 입력도 같은 `seed` 에 넣었다(11절 2번 권장안). 제목은 첫 줄 `# 제목` 으로 받는다(11절 1번 권장안).

두 부분이다. 서로 독립이라 따로 정하고 따로 넣을 수 있다.

- **A부(1~11절): 주고받는 형식.** 단계 지시문을 어떤 모양으로 보내고 본문을 어떤 모양으로 받는가. 여기서 "중심 양식"은 단계 지시문 JSON 이다.
- **B부(문서 끝): 글 목적별 뼈대 양식.** 글의 계획인 뼈대 JSON 이 글의 목적에 따라 어떤 양식을 갖는가.

범위는 설명문 파이프라인(`services/expository.py`)이다. A부에서 먼저 바꾸는 단계는 본문을 쓰는 5단계(작성)와 8단계(번역)다.
기존 라운드 파이프라인(`agents/builders.py`)은 이 설계안에 없다.

## 1. 목표

- 프롬프트는 지금처럼 단계마다 JSON 한 벌(중심 양식)만 둔다. 벤더별 사본을 만들지 않는다.
- 보내기 직전에 한 곳(변환기)에서 그 호출 모델에 맞는 메시지로 바꾼다.
- 긴 본문은 입력과 출력 모두 JSON 문자열 밖에 둔다.

## 2. 지금 구조

| 항목 | 지금 | 위치 |
|---|---|---|
| 프롬프트 | 단계마다 JSON 지시문 하나. Mongo 버전 문서 `prompts.expository.{단계}` | D-99 |
| 보내는 메시지 | system 은 고정 한 문장, user 는 `inputs` 를 채운 JSON 전체. 어느 벤더든 같다 | `agents/expository.py` `render_prompt`, `services/expository.py` `_call_until_valid` |
| 본문 입력 | 6·7·8단계가 `inputs.content_markdown` 으로 받는다. JSON 문자열이라 줄바꿈·따옴표가 이스케이프된다 | `render_prompt` 의 `json.dumps` |
| 본문 출력 | 5·8단계가 `{"title", "content_markdown"}` JSON 으로 낸다 | `5_write.json`, `8_translate.json` 의 `expected_output_schema` |
| 벤더 구조화 출력 | 쓰지 않는다(`call_text`). 출력은 코드가 읽고 검사한다 | `agents/expository.py` 16행 주석 |

저장소 `routes.json` 기준으로 5·8단계 모델은 `claude-fable-5-2` 하나다. 운영 버전의 라우트는 확인하지 않았다.

## 3. 근거

확인한 것:

- **본문을 JSON 에 싸면 결과가 나빠질 수 있다.** Aider 2024-08-14 "LLMs are bad at returning code in JSON": 코드를 JSON 으로 받게 하자 시험한 네 모델 모두 점수가 내려갔고(한 모델은 오차 범위 안), strict 모드도 낫지 않았다. 글쓴이가 든 원인은 `\"`·`\n` 이스케이프다. 2024년 모델, 코드 과제에 한정된 결과다.
- **긴 자료는 위, 지시는 아래.** Anthropic 문서: 긴 문서를 프롬프트 위쪽에, 질문·지시를 끝에 둔다(20k 토큰 이상의 자료, 복잡한 다문서 입력에서 최대 30% 개선). Gemini 문서도 같은 배치를 권한다("supply all the context first … instructions or questions at the very end").
- **구획은 XML 태그.** Anthropic 문서: 지시·맥락·입력을 태그로 나누면 오독이 준다. Gemini 문서: XML 태그나 마크다운 제목 중 하나를 골라 한 프롬프트 안에서 섞지 않는다.
- **Gemini 는 역할·지시·제약·출력 형식을 시스템 지시에 두라고 권한다.** Anthropic 문서는 역할을 시스템 프롬프트에 두라고만 한다.

확인하지 못한 것:

- xAI 문서에서 프롬프트 배치 지침을 찾지 못했다(시도한 주소 둘이 404).
- 이 블로그 글 크기(번역 입력 추정 1만 토큰 안팎)는 Anthropic 문서의 20k 기준보다 작다. 순서 효과는 작을 수 있다. 이 설계의 주된 근거는 순서가 아니라 이스케이프를 없애는 것이다.
- 효과 크기는 재지 않았다. 9절의 비교로 잰다.

따라서 지금 근거로는 **작성·번역 단계에서 벤더마다 달라야 할 것이 거의 없다**. 변환기를 두는 한 곳과 벤더(가족)별로 배치를 고르는 자리는 만들되, 처음 넣는 새 배치는 하나다. 벤더별 차이가 실제로 큰 곳은 JSON 을 받는 단계의 구조화 출력이고 10절에 적었다.

## 4. 설계

### 4.1 중심 양식 (단계 프롬프트 JSON)

지금 키(`system_config`·`task`·`inputs`·`constraints`·`expected_output_schema`·단계별 규칙 목록)는 그대로다. 두 가지가 더해진다.

| 키 | 뜻 | 없으면 |
|---|---|---|
| `documents` | 긴 글 입력. 키는 입력 이름, 값은 자리 표시다. `inputs` 와 이름이 겹치지 않는다 | 모든 입력이 `inputs` 다(지금과 같다) |
| `output_document` | 출력을 JSON 이 아니라 마크다운 문서로 받는다. `{"title": 설명, "body": 설명}` | `expected_output_schema` 로 JSON 을 받는다(지금과 같다) |

`expected_output_schema` 와 `output_document` 는 둘 중 하나만 둔다. 러너가 단계에 넘기는 값의 이름은 바뀌지 않는다: 어느 값이 `documents` 인지는 프롬프트가 정한다. 그래서 옛 프롬프트는 새 코드에서 지금과 똑같이 돈다.

`output_document` 는 5·8단계에만 둘 수 있다. 러너와 검사가 이 두 단계의 결과를 `title`·`content_markdown` 두 키로 읽기 때문이다.

### 4.2 배치 (변환기)

배치는 중심 양식과 채운 값을 받아 보낼 메시지를 만드는 코드 함수이고 이름으로 고른다(D-70: 데이터는 코드를 이름으로만 가리킨다).

| 이름 | system | user |
|---|---|---|
| `json_blob` | 지금의 고정 문장 | 값을 채운 JSON 전체. `documents` 도 JSON 안에 든다. 지금 동작이다 |
| `documents_first` | 고정 문장(`<documents>` 는 자료라는 말 포함) | `<documents>` 안에 문서마다 `<이름>` 태그로 원문 그대로 → `<instructions>` 안에 지시문 JSON. JSON 의 `documents` 값은 "위 `<이름>` 블록" 이라는 표시로 바뀐다 |

`documents` 가 없는 프롬프트는 어느 배치에서나 `json_blob` 과 같은 메시지가 나간다. 즉 배치를 바꿔도 1~5·9단계가 받는 메시지는 달라지지 않는다.

어느 배치를 쓸지는 버전 문서의 조절 값이다.

```json
"parameters": { "expository": { "layouts": { "default": "json_blob" } } }
```

키는 프롬프트 가족(`registry.prompt_family`: claude·gemini·grok·haiku)이고 없는 가족은 `default` 다. 코드 기본값이 `json_blob` 이라 이 필드가 없는 옛 버전 문서는 지금과 같이 돈다. 바꾸기는 기존 명령이다.

```bash
python -m cli agents pipeline edit-param 'expository.layouts={"default":"documents_first"}'
```

Gemini 가 권하는 "지시를 시스템 지시로" 배치는 지금 만들지 않는다. 5·8단계를 Gemini 로 돌릴 때 이름 하나를 더하면 된다.

### 4.3 문서 출력 읽기

`output_document` 단계의 system 문장은 "첫 줄에 `# 제목`, 빈 줄, 그 아래 본문. JSON·설명·문서를 감싼 코드 펜스 없이" 다. 읽는 규칙:

1. 문서 전체가 코드 펜스에 싸여 있으면(첫 줄이 펜스 여는 줄, 마지막 줄이 닫는 줄) 바깥 펜스를 뗀다.
2. 첫 줄이 `# ` 로 시작하지 않으면 오류다. 그 줄이 `title` 이다.
3. 나머지가 `content_markdown` 이다. 비어 있으면 오류다.

본문은 `##`·`###` 제목만 쓰므로 첫 줄 `#` 은 제목과 섞이지 않는다. 결과는 지금과 같은 `{"title", "content_markdown"}` dict 라서 `check_article`·`check_translation`·조립·실행 문서는 바뀌지 않는다. 오류는 지금처럼 직전 출력 뒤에 붙여 다시 부른다. 재시도 안내 문장만 "JSON 객체" 대신 "제목 줄과 본문 전체" 로 바뀐다.

### 4.4 실행 문서·영수증

- `expository_runs` 는 바뀌지 않는다. `stage_prompts` 는 지금처럼 값을 채운 중심 양식(벤더와 무관한 JSON 문자열)이다.
- 그래서 배치만 바꾼 뒤 같은 실행을 이어 하면 끝난 단계는 다시 부르지 않는다(모델을 바꿨을 때와 같다). 다시 부르려면 `--redo-from` 이다.
- 영수증의 `messages` 는 지금처럼 실제로 보낸 메시지다. 어느 배치였는지는 영수증의 `pipeline_version` 이 가리키는 버전의 조절 값에 있다.

### 4.5 검증

- R-13: 프롬프트는 `inputs` 와, `expected_output_schema`·`output_document` 중 정확히 하나가 있어야 한다. `documents` 는 객체이고 `inputs` 와 이름이 겹치지 않는다. `output_document` 는 5·8단계에만 둔다.
- R-09: `expository.layouts` 에 `default` 가 있고 값이 모두 등록된 배치 이름이다.

## 5. 예시: 8단계 번역

바뀐 프롬프트(바뀌는 부분만):

```json
{
  "task": "Rewrite documents.content_markdown as a natural Korean technical blog post and write its Korean headline. ...",
  "inputs": { "title": "{{title}}", "terms": "{{terms}}" },
  "documents": { "content_markdown": "{{content_markdown}}" },
  "output_document": {
    "title": "Korean headline, at most 40 characters",
    "body": "Full Korean markdown body with every anchor preserved"
  }
}
```

`documents_first` 로 나가는 user 메시지:

```text
<documents>
<content_markdown>
<!-- sec:intro -->
## 1. Why the Transaction Did Not Start
(영어 본문 원문 그대로. 이스케이프 없음)
</content_markdown>
</documents>

<instructions>
{ ...지시문 JSON. "documents": {"content_markdown": "given above in <content_markdown>"} ... }
</instructions>
```

모델 출력:

```text
# 셀프 호출이 트랜잭션을 건너뛰는 이유

<!-- sec:intro -->
## 1. 트랜잭션이 시작되지 않은 이유
...
```

## 6. 단계별 적용

| 단계 | 입력 | 출력 |
|---|---|---|
| 5 작성 | 그대로(뼈대는 구조가 있는 JSON 이라 `inputs` 에 둔다) | `output_document` |
| 8 번역 | `content_markdown` 을 `documents` 로 | `output_document` |
| 6 본문 사실 확인, 7 요약·용어 | `content_markdown` 을 `documents` 로 (프롬프트만 고치면 된다) | 그대로 JSON |
| 1~4, 9 | 그대로 | 그대로 |

6·7단계 입력을 같이 바꾸는 것을 권한다. 코드가 더 들지 않고, 6단계는 패치의 `before` 가 본문의 정확한 부분 문자열이어야 해서 버린 패치 수(`rejected_patches`)로 효과를 잴 수 있다.

## 7. 바뀌는 파일

- `app/agents/expository.py`: `parse_prompt`(두 출력 종류·`documents`), 배치 두 개와 메시지 만들기, 문서 출력 읽기, system·재시도 문장, `Stage` 의 문서 출력 가능 표시
- `app/services/expository.py`: `_call_until_valid` 가 배치로 메시지를 만들고 출력 종류에 맞게 읽는다
- `app/models/pipeline.py`: `Parameters.expository.layouts`
- `app/services/pipeline_validation.py`: R-13, R-09
- `pipeline/expository/prompts/5_write.json`·`8_translate.json`(·`6_…`·`7_…`), `pipeline/expository/README.md`
- `agents_design/09_pipeline_as_data.md`(D-103, R-13 행), `CLAUDE.md` 진행 줄
- 테스트(가짜 LLM): 옛 프롬프트는 메시지가 바이트까지 같다, `documents_first` 는 문서를 원문 그대로 올린다, 문서 출력 읽기 네 경우, 5·8단계가 문서로 끝까지 도는 실행, R-13·R-09

`AgentCallService` 는 바꾸지 않는다(`call_text` 가 이미 재시도 안내를 인자로 받는다). 조절 값 필드가 늘어 `pipeline/baseline/` 해시 테스트에 걸리면 기준을 다시 내보낸다.

## 8. 운영 반영 순서

각 단계가 버전 하나라서 따로 되돌릴 수 있다.

1. 배포. 동작은 바뀌지 않는다(옛 프롬프트, 기본 배치 `json_blob`).
2. `python -m cli.expository seed`: 새 프롬프트. 5·8단계 출력이 문서가 된다.
3. `edit-param 'expository.layouts={"default":"documents_first"}'`: 본문 입력이 JSON 밖으로 나온다.

## 9. 효과 확인

기존 명령만으로 같은 뼈대에서 전후를 비교할 수 있다.

1. 2번(seed) 전에 새 실행을 9단계까지 돌리고 `show <실행 id> --out a.md`.
2. 2·3번을 한 뒤 같은 실행을 `run --run-id <실행 id> --redo-from 5` 로 다시 돌리고 `show --out b.md`. 1~4단계 결과는 그대로 쓴다.
3. 비교: 두 글을 읽는다. 영수증에서 5·8단계의 출력 토큰·재시도 횟수·잘림 여부, 실행 문서의 `rejected_patches` 수.

글 한 편씩의 비교라 품질 차이를 증명하지는 못한다. 토큰·재시도·버린 패치의 차이와, 나빠진 곳이 없는지는 볼 수 있다. 비용은 작성·번역 호출이 각각 출력 2만~3만 토큰이라고 보면 Fable 단가(출력 100만 토큰당 40달러) 기준 호출당 1달러 안팎, 비교 한 번에 몇 달러로 추정한다(실측 아님).

## 10. 이 설계안에 없는 것

- **JSON 을 받는 단계의 벤더 구조화 출력.** 벤더 차이가 실제로 큰 곳이다. Claude 는 스키마 순서대로(필수 먼저) 생성하고 길이·범위 제약을 지원하지 않으며 인용이 켜진 검색과 같이 쓰면 400 이다. Gemini 는 `minLength`·`maxLength` 를 떼고 복잡한 스키마에 400 을 내며 3·4 계열은 검색과 같이 쓸 수 있다(미리보기). xAI 는 길이·범위 제약을 지원하고 Grok 4 계열은 검색과 같이 쓸 수 있다. Gemini·xAI 의 키 순서 보장은 문서에서 찾지 못했다. Gemini 항목은 문서 뒷부분을 요약으로만 읽었으므로 그 단계를 설계할 때 다시 확인한다.
- **지시문 JSON 자체를 태그 글로 바꾸기.** 짧은 문자열이라 이스케이프가 거의 없고 근거가 없다.
- **기존 라운드 파이프라인.** 벤더별 프롬프트 사본을 한 벌로 합치는 일과, 요약 길이 위반이 본문 전체를 다시 쓰게 하는 계약 분리는 따로 정한다.

## 11. 정할 것

1. 제목을 본문 첫 줄 `# 제목` 으로 받는다(권장). 대안은 `<title>` 태그다.
2. 6·7단계 입력을 같은 seed 에 넣는다(권장).
3. 기본 배치를 `documents_first` 로 두기 전에 9절 비교를 한 번 돌린다(권장).

---

# B부. 글 목적별 뼈대 양식 (2026-11-17 추가)

사용자 지적: 글의 목적에 따라 중심 JSON 의 양식이 바뀌어야 한다. 여기서 중심 JSON 은 글의 계획인 **뼈대**(2단계 출력, 3·4·5단계 입력)로 읽었다. 이 읽기가 틀리면 B부 전체를 다시 쓴다.

## B1. 지금: 양식이 하나이고 "문제 → 원인 → 해결" 이다

이름은 설명문 파이프라인이지만 뼈대는 문제 해결 틀 하나로 고정돼 있고, 그 틀이 1단계부터 코드까지 들어 있다.

| 곳 | 고정된 내용 |
|---|---|
| 1단계 프롬프트 | 주제는 "개발자가 마주치는 동작이나 실패"(`expository_only`), 논제는 "그 문제를 막거나 푸는 설계 결정"(`arguable_thesis`), `focus_mechanisms` 는 "문제가 생기는 이유의 서로 다른 부분" |
| 2단계 뼈대 스키마 | `introduction.problem_statement`(문제 상황), `deep_dive_sections[].mechanism_explanation`(그 상황에서 A → B → C), `practical_application.architecture_pattern`(문제를 푸는 설계)·`exception_case` |
| 3단계 검토 기준 | "빼도 문제의 원인이 그대로 설명되면 뺀다"(`weak_relevance`) |
| 5단계 글 구조 | intro(문제 상황) → concept → mech-N → application(해결) → exceptions → conclusion |
| 코드 | `expository_checks.py` 의 `_INTRO_KEYS`·`_SECTION_KEYS`·`_APPLICATION_KEYS`·`_check_section_rules`·`article_section_ids`, `expository_assembly.py` 의 전체 정리 표(`deep_dive_sections` 의 `sub_topic`·`key_takeaway`), `services/expository.py` 의 `TOPIC_KEYS` |

그래서 "B-Tree 인덱스는 어떻게 생겼나" 같은 개념 설명 주제는 문제 상황과 해결 설계를 지어내야만 이 뼈대에 들어간다.

## B2. 제안: region 컨테이너 하나 + 글 양식

뼈대의 겉모양(컨테이너)은 하나로 고정하고, 목적에 따라 달라지는 것은 region 의 역할 구성으로 둔다. 붙여주신 구조(region 안은 핵심 문장 → 상세 풀이 → 예시, 본론 region 마다 역할 하나)를 그대로 따른다.

```json
{
  "form": "concept",
  "core_message": "(논제 그대로)",
  "regions": [
    {
      "role": "structure",
      "heading": "섹션 제목이 될 명사구",
      "key_sentence": "이 region 의 핵심 문장 하나",
      "elaboration": "상세 풀이",
      "example": "구체적 예시",
      "takeaway": "전체 정리 표에 들어갈 명사구"
    }
  ]
}
```

- 슬롯 이름은 모든 양식이 같다. 그래서 검사·조립 코드는 양식마다 갈라지지 않는다: 슬롯이 다 있는가, 핵심 문장이 한 문장인가, 역할의 순서와 개수가 그 양식과 맞는가.
- 섹션 앵커는 모델이 쓰지 않고 코드가 양식의 역할 목록에서 만든다.

**글 양식**은 역할 목록(순서, 개수 범위, 그 역할이 말해야 하는 것)과 논제의 꼴을 적은 JSON 이다. 버전 문서의 `prompts` 에 가족 하나(`expository_form`)를 더해 양식 id 를 이름으로 둔다. 고치기는 기존 `edit-prompt` 이고, 새 양식을 넣는 데 배포가 필요 없다.

양식 초안(역할 구성은 붙여주신 안, 마지막 줄은 지금 양식을 옮긴 것):

| 양식 | 쓸 때 | region 역할 |
|---|---|---|
| `concept` 개념·구조 | 대상이 무엇이고 어떻게 돌아가는지 | 정의·윤곽 → 구성 요소(What) → 작동 원리(How) → 배경·성질(Why) → [분류·대조, 선택] → 종합 |
| `procedure` 절차·과정 | 단계의 흐름이 대상 | 정의·윤곽 → 단계 3개 이상(단계마다 region 하나) → 종합 |
| `taxonomy` 분류·유형 | 여러 유형이 대상 | 정의·윤곽 → 분류 기준 → 유형 2개 이상 → 종합 |
| `diagnosis` 문제 원인 | 증상이나 실패가 출발점 | 문제 상황 → 개념 → 원인 3개 이상 → 해결 → 예외 → 종합 |

## B3. 단계별로 바뀌는 것

| 단계 | 변화 |
|---|---|
| 1 주제·논제 | 입력에 양식 목록(id·쓸 때)이 들어가고 출력에 `form` 이 생긴다. 논제와 본론 초점의 꼴은 고른 양식을 따른다. 사람이 `--form` 으로 정할 수도 있다 |
| 2 뼈대 | 입력에 고른 양식. 출력은 region 컨테이너 |
| 3 검토 | 빼기·채우기는 개수가 늘어나는 역할(원인·단계·유형)에만 뜻이 있다. 그런 역할이 없는 `concept` 은 호출하지 않는다(B9) |
| 4 뼈대 사실 확인 | 주장 위치의 경로만 바뀐다 |
| 5 작성 | 글 구조가 "region 하나에 섹션 하나, 순서대로" 가 되고, 역할별 쓰는 법은 양식에서 온다 |
| 6~9 | 섹션 id 가 양식에서 온다는 것만 다르다 |
| 코드 | 검사·조립이 region 기준의 한 벌이 된다. 1~5단계 프롬프트는 전부 다시 쓴다 |

## B4. A부와의 관계, 비용

- A부는 어떻게 보내는가, B부는 무엇을 계획하는가다. 겹치는 파일은 5단계 프롬프트 하나다.
- B부가 훨씬 크다: 1~5단계 프롬프트 전부와 `expository_checks.py` 대부분, 조립, 테스트 픽스처가 바뀐다. 대신 글의 내용에 직접 닿는 것은 B부다.
- 2026-11-11 뼈대 테스트 뒤에 정한 규칙(`principle` 두 문장, `mechanism_explanation` 인과 한 문장, 해결은 application 에서만)은 `diagnosis` 양식의 역할 설명으로 옮겨야 하고, 옮긴 뒤 그 양식이 전과 같은 글을 내는지 다시 봐야 한다.
- 뼈대 키가 바뀌므로 진행 중인 실행은 새 코드에서 이어 할 수 없다.
- 순서는 A부 먼저를 권한다. 작고, 같은 뼈대로 전후를 비교할 수 있어 형식의 효과만 따로 볼 수 있다.

## B5. 정한 것과 권장안

정한 것:

- 지금의 "문제 → 원인 → 해결" 양식은 `diagnosis` 로 남긴다(2026-11-17 사용자 결정).

권장안으로 두고 설계를 이어 간 것(사용자가 다르게 정하면 바꾼다):

1. 양식 목록은 위 넷이다.
2. 양식은 1단계 모델이 고르고 사람이 `--form` 으로 덮어쓴다.
3. `concept` 의 논제. 붙여주신 안대로 "이 대상은 어떠하다" 는 정의로 두면, 2026-11-11 에 본 문제(논제가 단순 사실이라 공식 문서 대신 읽을 이유가 없다)가 다시 생길 수 있다. 정의는 도입 region 에 두고 논제는 Why region 의 주장(왜 이렇게 생겼는가, 이 구조에서 무엇이 따라 나오는가)으로 잡기를 권한다.
4. 선택 region "분류·대조" 는 2026-11-11 의 비교형 주제 금지와 다르다고 봤다: 주제는 여전히 대상 하나이고, region 하나만 헷갈리는 개념과의 경계를 긋는다.
5. 3단계 검토는 개수가 늘어나는 본론 역할이 없는 양식(`concept`)에서 부르지 않는다.

## B6. 글 양식 JSON 의 모양

```json
{
  "label": "Concept and structure",
  "use_when": "주제가 이 양식에 맞는 조건",
  "topic": "주제 문구의 꼴",
  "thesis": "논제의 꼴",
  "focus_points": "본론 초점 목록의 꼴",
  "overview_header": ["Aspect", "Takeaway"],
  "outline_rules": ["이 양식에만 붙는 뼈대 규칙"],
  "write_rules": ["이 양식에만 붙는 작성 규칙"],
  "regions": [
    {
      "role": "structure",
      "section": "structure",
      "min": 1,
      "max": 1,
      "focus": true,
      "example": true,
      "sentences": { "elaboration": [2, 4] },
      "outline": "뼈대의 이 region 이 말해야 하는 것",
      "write": "이 region 을 섹션으로 쓰는 법"
    }
  ]
}
```

| 필드 | 읽는 곳 | 뜻 |
|---|---|---|
| `use_when`·`topic`·`thesis`·`focus_points` | 1단계 | 양식을 고르는 기준과, 고른 양식에서 주제·논제·본론 초점이 가져야 할 꼴 |
| `regions[].outline` | 2·3단계 | 그 역할의 region 이 뼈대에서 말해야 하는 것 |
| `regions[].write` | 5단계 | 그 region 을 섹션으로 쓰는 법 |
| `outline_rules`·`write_rules` | 2·3단계, 5단계 | 그 양식에만 붙는 규칙. 단계 프롬프트의 공통 규칙 뒤에 붙는다 |
| `section` | 코드 | 섹션 앵커 id. `max` 가 1 이면 그대로, 아니면 `{section}-{n}` |
| `min`·`max` | 코드 | 그 역할의 region 수. `max` 가 없으면 상한이 없다 |
| `focus` | 코드 | 본론 region 이다. 1단계 `focus_points` 와 1:1 이고, 3단계 검토의 대상이며, 전체 정리 표의 행이다 |
| `example` | 코드 | false 면 예시 슬롯을 비운다(도입·종합) |
| `sentences` | 코드 | 슬롯별 문장 수 범위. 없으면 재지 않는다. `key_sentence` 는 언제나 한 문장이다 |
| `overview_header` | 코드 | 전체 정리 표의 머리 두 칸 |

주제 필드 이름 `focus_mechanisms` 는 `focus_points` 로 바꾼다. 개념·분류 양식에서 "mechanisms" 라는 이름이 모델을 메커니즘 설명 쪽으로 끌기 때문이다.

## B7. `concept` 초안

| 역할 | 섹션 | 수 | 본론 | 뼈대에 적는 것 |
|---|---|---|---|---|
| `definition` | `intro` | 1 | | 대상을 한 문장으로 정의하고, 글이 다루는 범위와 순서를 적는다. 예시 없음 |
| `structure` | `structure` | 1 | ✓ | 대상을 이루는 부분들의 이름과 각 부분의 역할 |
| `mechanism` | `mechanism` | 1 | ✓ | 그 부분들이 어떤 순서와 규칙으로 맞물려 돌아가는가 |
| `rationale` | `rationale` | 1 | ✓ | 왜 이렇게 생겼는가, 이 구조에서 반드시 따라 나오는 성질. 논제를 싣는다 |
| `contrast` | `contrast` | 0~1 | ✓ | 세부 종류, 또는 헷갈리는 개념 하나와 경계가 어디인가. 우열을 매기거나 추천하지 않는다 |
| `synthesis` | `conclusion` | 1 | | 나눠 설명한 구조·원리·이유를 하나로 묶어 논제를 다시 말한다. 예시 없음 |

`procedure` 는 가운데 세 역할 자리에 `step`(섹션 `step-N`, 3개 이상)이, `taxonomy` 는 `criteria`(1개)와 `type`(섹션 `type-N`, 2개 이상)이 온다. 영어 문구는 구현할 때 쓴다.

## B8. `diagnosis`: 지금 양식을 옮긴 것

섹션 id 는 지금과 같다. 2026-11-11 에 정한 규칙은 문장 수와 양식 규칙으로 옮긴다.

| 지금 필드 | 역할(섹션) | 슬롯 |
|---|---|---|
| `introduction.problem_statement` | `problem`(`intro`) | `key_sentence` 관찰된 잘못된 결과, `elaboration` 실행되는 코드 경로. 예시 없음 |
| `introduction.concept_definition` | `concept`(`concept`) | `key_sentence` 핵심 개념의 정의, `elaboration` 구조상 역할. 예시 없음 |
| `deep_dive_sections[].sub_topic` | `cause`(`mech-N`, 3개 이상, 본론) | `heading` |
| `principle` 첫 문장 | 〃 | `key_sentence` |
| `principle` 둘째 문장 | 〃 | `elaboration` (정확히 한 문장: 상황과 무관하게 따라 나오는 것) |
| `mechanism_explanation` | 〃 | `example` (정확히 한 문장: A 가 실행되면 B 때문에 C 가 나온다) |
| `key_takeaway` | 〃 | `takeaway` (10단어 이하) |
| `practical_application.architecture_pattern` | `fix`(`application`) | `key_sentence` 설계 결정, `elaboration` 구체 설계, `example` 도입의 상황이 어떻게 풀리는가 |
| `practical_application.exception_case` | `exceptions`(`exceptions`) | `key_sentence`·`elaboration` 논제가 성립하지 않는 경우와 그 조건. 예시 없음 |
| 없음(지금은 5단계가 만든다) | `synthesis`(`conclusion`) | `key_sentence` 원인들에 비춰 다시 말한 논제. 점검 질문 목록은 지금처럼 5단계가 쓴다 |

양식 규칙으로 가는 것: "원인 region 은 문제가 왜 생기는지만 말하고 해결은 `fix` 에만 둔다"(뼈대·작성 둘 다), "1인칭 일화 금지".

## B9. 코드가 하는 일

- **뼈대 검사 한 벌.** region 마다 `role`·`heading`·`key_sentence`·`elaboration` 이 있고, `example` 은 양식이 끄지 않은 역할에, `takeaway` 는 본론 region 에 있다. 역할의 순서와 개수가 양식과 맞다. `key_sentence` 는 한 문장, `takeaway` 는 10단어 이하, `sentences` 가 적힌 슬롯은 그 범위다. 본론 region 수는 `focus_points` 수와 같다.
- **섹션 id.** 뼈대와 양식에서 코드가 만든다. 5단계 검사, 7단계 요약 대상, 조립이 이 목록을 쓴다.
- **3단계.** 본론 역할 중 `max` 가 1 보다 큰 것이 있을 때만 부른다. 빼기·채우기는 그 역할의 region 에만 허용하고 나머지는 지금처럼 글자까지 그대로여야 한다. 부르지 않는 양식은 뼈대를 그대로 넘긴다.
- **전체 정리 표.** 본론 region 의 `heading`·`takeaway` 를 행으로, 머리는 `overview_header` 로 만들어 마지막 섹션 끝에 붙인다.
- **1단계 검사.** `form` 이 버전 문서에 있는 양식 id 다. `focus_points` 수가 그 양식의 본론 region 수 범위 안이다. 비교형 주제 금지는 모든 양식에 그대로다.
- **양식 없는 버전.** 최신 버전에 양식이 없으면 러너는 호출 전에 멈춘다(지금 "설명문 프롬프트가 없다" 와 같은 방식). 배포와 `seed` 사이에 시작한 실행이 토큰을 쓰지 않는다.

## B10. 바뀌는 파일, 반영 순서, 확인

- 넣는 입력: `pipeline/expository/forms/{concept,procedure,taxonomy,diagnosis}.json`(새로), `prompts/1_topic_thesis.json`~`5_write.json`(양식을 입력으로 받는 공통 문구로 다시 쓴다), `examples/*.topic.json`(`form`·`focus_points`), `README.md`
- 코드: `app/agents/expository.py`(양식 읽기·섹션 id), `app/services/expository.py`(단계 입력에 양식, 3단계 건너뛰기, `TOPIC_KEYS`), `expository_checks.py`(뼈대 검사 한 벌), `expository_assembly.py`(전체 정리 표), `app/models/pipeline.py`·`app/agents/prompts/registry.py`(가족 `expository_form`), `pipeline_validation.py`(R-08·R-13), `pipeline_versions.py`(`seed` 가 `forms/` 를 읽는다, 내보내기·들여오기), `cli/expository.py`(`--form`)
- 문서: 09 문서(D-104, R-13 행), `CLAUDE.md` 진행 줄
- 테스트: `tests/factories.py` 의 뼈대 픽스처, 검사·조립·러너 테스트

`pipeline_versions.py` 는 2026-11-17 현재 다른 세션이 고치는 중이라(커밋 전) B부 구현은 그 변경이 커밋된 뒤에 한다. A부는 그 파일을 건드리지 않는다.

반영 순서는 배포 다음에 `seed` 다. 그 사이에는 러너가 양식 없음으로 멈춘다. 이전 이미지로 되돌릴 때는 프롬프트도 옛것으로 다시 넣어야 한다.

확인은 실제 실행 둘이다. `diagnosis` 는 기존 예시 주제(Spring 셀프 호출)로 돌려 전에 나온 글과 견주어 나빠진 곳이 없는지 보고, `concept` 은 개념 주제 하나로 돌려 문제 상황을 지어내지 않는지 본다. 전체 실행 한 번의 비용은 재지 않았다.
