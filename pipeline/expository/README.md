# 설명문 파이프라인 (expository)

기존 `pipeline/baseline/`(A·B·C 세 초안, 교차 리뷰, 다듬기)과 별개인 새 파이프라인이다. 프롬프트는 `prompts/`, 단계별 모델은 `routes.json`, 러너는 `app/services/expository.py` 다. Mongo·상태 기계·발행 없이 로컬 CLI 로 글 하나를 끝까지 만든다.

## 정한 것 (2026-11-11)

- 설명문만 쓴다. A vs B 비교 구조는 쓰지 않는다.
- 번역 전 단계는 모두 영어로 진행하고, 완성된 글을 한 번 번역한다.
- 뼈대의 섹션은 3개 이상이고 상한은 없다. 리뷰가 연관도가 낮거나 중복된 섹션을 빼고, 3개 미만이 되면 리뷰어가 모자란 만큼 채운다.
- 섹션은 문제의 원인을 설명한다. 논제의 해법은 실무 적용(`application`)에서 처음 나온다.
- `principle` 은 중심문장과 결론 두 문장이다. 작성 단계가 그 사이를 채운다.

## 실행

```bash
python -m cli.expository run --theme spring
```

- `--topic-file topic.json`: 1단계 결과(topic·core_argument·focus_mechanisms·out_of_scope)를 직접 준다. 1단계를 부르지 않는다.
- `--stop-after 4`: 그 단계까지만 돌린다. `--run-dir <폴더>` 로 이어 한다.
- `--max-words 3000`: 5단계 영어 본문의 단어 상한(코드 제외). 3000 은 기존 `draft_max_chars` 20000자의 60~70%를 어림한 값이고 재 보지 않았다.
- 환경 변수의 벤더 키(`ANTHROPIC_API_KEY`·`GOOGLE_API_KEY`)를 쓴다. `routes.json` 후보 중 키가 있는 첫 벤더를 부른다. `POST_BE_URL` 이 있으면 1단계에 카테고리 목록을 넣고, 없으면 카테고리 없이 고른다.

### 운영 컨테이너에서

키가 운영 환경 변수에 있으므로 CloudType 컨테이너 터미널(작업 폴더 `/srv`)에서 돌린다. 이미지에 이 폴더가 들어 있고 `runs/` 만 쓸 수 있다(Dockerfile). 한 번에 수십 분 걸리므로 터미널이 끊겨도 돌도록 `nohup` 으로 띄운다.

```bash
nohup python -m cli.expository run --topic-file pipeline/expository/examples/spring_self_invocation.topic.json > /tmp/expository.log 2>&1 &
```

```bash
tail -f pipeline/expository/runs/*/log.jsonl
```

호출이 끝날 때마다 한 줄씩 늘어난다(단계·모델·토큰·검사 오류). 끝나면 `cat /tmp/expository.log` 에 실행 폴더와 성공·실패가 나오고, 결과는 `cat pipeline/expository/runs/<시각>/final.md` 로 본다. 컨테이너 파일은 재배포·재시작 때 사라지므로 필요한 결과는 그 전에 옮긴다.

결과는 `runs/<시각>/`(git 제외)에 단계마다 남는다.

| 파일 | 내용 |
|---|---|
| `{단계}.prompt.json` | 입력을 채운 프롬프트 |
| `{단계}.attemptN.txt` | 모델 원문(시도마다) |
| `{단계}.json` | 검사를 통과한 결과 |
| `6_article_fact_check.article.md`, `.rejected_patches.json` | 패치를 적용한 영어 본문, 버린 패치 |
| `7_summary_glossary.article.md` | 표·용어 정리를 넣은 영어 본문(번역 입력) |
| `final.md`, `final.json` | 한국어 제목·요약·본문, 키워드·카테고리 |
| `log.jsonl` | 호출마다 모델·토큰·검사 오류 |

같은 `--run-dir` 로 다시 돌리면 결과 파일이 있고 입력을 채운 프롬프트가 지난번과 같은 단계는 건너뛴다. 앞 단계 결과나 프롬프트 파일을 고치면 그 단계부터 다시 부른다. 한 단계를 다시 부르려면 그 단계의 `.json` 을 지운다.

## 단계

| # | 프롬프트 | 입력 | 출력 | 웹 검색 |
|---|---|---|---|---|
| 1 | `1_topic_thesis.json` | theme_keyword, avoid_topics, categories | topic, core_argument, focus_mechanisms(3개 이상), out_of_scope, keywords, 카테고리 쌍 | 아니오 |
| 2 | `2_outline.json` | 1의 topic, core_argument, focus_mechanisms, out_of_scope | 뼈대 | 아니오 |
| 3 | `3_outline_review.json` | 2의 입력 + outline | outline, review_log | 아니오 |
| 4 | `4_outline_fact_check.json` | topic, outline | outline(고친 것), claims | 예 |
| 5 | `5_write.json` | outline, out_of_scope, max_words | title, content_markdown(영어) | 아니오 |
| 6 | `6_article_fact_check.json` | outline, content_markdown | claims, patches | 예 |
| 7 | `7_summary_glossary.json` | content_markdown, section_ids | section_summaries, terms | 아니오 |
| 8 | `8_translate.json` | title, 표·용어를 넣은 content_markdown, terms | 한국어 title, content_markdown | 아니오 |
| 9 | `9_summarize.json` | 한국어 title, 서론 첫 문단 | summary(메타 설명) | 아니오 |

러너는 `inputs` 의 `{{...}}` 를 문자열로 치환하지 않고 `inputs` 객체를 통째로 바꾼다(배열·객체가 그대로 들어간다). 벤더 구조화 출력은 쓰지 않고 JSON 을 글로 받아 꺼낸다(검색 단계는 구조화 출력과 같이 쓸 수 없다).

## 단계 사이에 코드가 하는 일

`app/services/expository_checks.py` 가 검사하고, 걸리면 직전 출력과 오류 목록을 대화에 붙여 같은 단계를 두 번까지 다시 부른다. 그래도 걸리면 멈춘다.

- 1 뒤: focus_mechanisms 3개 이상, keywords 3개, 카테고리 쌍이 categories 에 있다, topic 에 `vs`·`versus`·`difference between` 이 없다. `--topic-file` 로 준 값은 앞의 네 필드만 본다.
- 2 뒤: `core_message == core_argument`, 섹션 수 == focus_mechanisms 수, principle 두 문장, mechanism_explanation 한 문장, key_takeaway 10단어 이하, 지시문 낱말(`toy project`, `undergraduate`, `dilemma`, 필드 이름)이 없다.
- 3 뒤: 출력 섹션마다 입력 섹션과 글자까지 같거나 review_log 에 `added` 로 있다. 빠진 섹션은 `removed` 로 있다. deep_dive_sections 밖 필드는 입력과 같다. 3개 이상 남았는데 더하지 않았다.
- 4 뒤: `corrected_text` 가 있는 REFUTED 주장의 location 만 바뀌었다. `blocking` 주장이 있으면 멈춘다. `2_outline.json` 을 지우고 다시 돌린다.
- 5 뒤: 섹션 앵커가 `intro`, `concept`, `mech-1`…`mech-N`, `application`, `exceptions`, `conclusion` 순서다. 기존 게이트(`services/validation.py`)의 코드 블록·`code-N` 앵커·언어 태그·변환·코드 주석 검사, 표 없음, 지시문 낱말 없음.
- 6 뒤(코드): 패치의 `before` 가 그 섹션에 정확히 한 번 나올 때만 바꾸고, 바꾼 본문이 5의 검사에 걸리면 되돌린다. 버린 패치는 `rejected_patches.json` 에 남는다.
- 7 뒤(코드): `conclusion` 을 뺀 섹션 끝마다 `**📊 Summary: …**` 표, 결론 끝에 뼈대의 sub_topic | key_takeaway 로 `**📊 Overview**` 표, 정의가 있는 용어로 `<!-- sec:glossary -->` 용어 정리 절을 붙인다.
- 8 뒤: 앵커 목록·순서와 코드 블록별 줄 수가 영어본과 같다. 제목 40자 이하, 괄호·콜론 없음. 기존 게이트의 길이(`draft_min_chars`~`draft_reject_chars`, 코드 기본값 사용)·변환·합쇼체·명령형·코드 주석 검사.
- 9 뒤: 요약 150자 이하, "이 글"로 시작하지 않는다.

## 기존 프로젝트에서 가져온 규칙

- 섹션·코드 앵커, 허용 마크다운, 언어 태그와 `-snippet`, 긴 코드 블록 주석: `app/agents/prompts/v4/claude/write_draft.md`, `app/services/validation.py`
- 합쇼체: 09 문서 D-86
- 용어 표기(통용 한국어 용어, 음차 금지, 본문 첫 등장에 영어 원어 괄호): v4 `write_draft.md`·`propose_topic.md`
- 한국어 제목 규칙(40자, 글 형태별 제목 꼴): v4 `write_draft.md`
- 사실 확인의 주장 종류·판정·"기억으로 판단 금지": v4 `claude/fact_check.md`. 사실 확인 첫 후보는 Gemini 다(D-92). 기존은 검색 호출을 글로 받아 `FACT_TRANSFORM` 으로 JSON 으로 바꾼다. 여기서는 JSON 을 글로 받아 바로 꺼낸다. 꺼내기 실패가 잦으면 기존처럼 변환 단계를 둔다.
- 용어 정의는 사전체 명사구: 09 문서 D-88, `haiku/glossary.md`
- 번역에서 피할 패턴: 09 문서 D-90 기본 문체 패턴 D-14·D-8·D-10·T-1
- 메타 설명 규칙: v4 `common/summarize.md`(D-95)를 `9_summarize.json` 으로 옮겼다.
- 단계 모델: 기존 라우트와 같은 모델 이름(claude-opus-5-5, gemini-4-argon, claude-haiku-5-5). 작성·번역은 `max_output_tokens` 32000 이다(기존 24000 은 첫 시도마다 잘렸다).

## 아직 없는 것

- 실제 모델로 돌린 결과. 단위 테스트는 가짜 LLM 이다(`tests/services/test_expository*.py`, `tests/agents/test_expository.py`). 2단계 뼈대만 대화 세션에서 한 번 돌렸다(2026-11-11, 영어 출력으로 바꾸기 전 버전).
- 발행. `final.json` 을 Post BE 로 올리는 길은 없다.
- 실행이 고칠 수 없는 사실 오류(blocking)에서 뼈대를 자동으로 다시 만드는 것. 지금은 멈추고 사람이 다시 돌린다.
