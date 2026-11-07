# 08. 용어 사전과 제작 기록

파이프라인이 발행한 글에 붙일 두 기능의 설계다. 2026-11-07 FastAPI 쪽(5절 G-1~G-5)을 구현해 02~07 에 반영했고(9절), vans_story 쪽(G-6)은 같은 날 이어서 작업했다. 구현이 설계와 달라진 곳은 본문에 적었다(선택 라우트, 템플릿 위치, 후보 검사의 자리, 병합 제안의 범위).

- **용어 사전(glossary).** 글이 정의 없이 쓴 전제 밖 전문용어를 글마다 뽑아 뜻을 붙이고, 블로그가 첫 등장에 툴팁을 띄운다. 같은 사전이 작성자의 표기 표준("스테이트리스"가 아니라 "무상태(stateless)") 노릇도 한다.
- **제작 기록(provenance).** 글마다 어느 벤더의 어느 모델이 어느 단계에서 무엇을 했는지를 영수증(`agent_calls`)에서 모아 글 단위 문서 하나로 남긴다. 영수증이 정리돼도 남는 기록이 목적이다.

배경: 2026-11-07 사용자가 생성된 글을 읽고 (1) 전제 밖 용어가 정의 없이 쓰인다, (2) 주제 제안 때 생긴 음차가 글 전체에 퍼진다, (3) 글마다 어느 AI 가 무엇을 했는지 남겨야 한다고 지적했다. 프롬프트 v4(D-62) 품질 작업과는 별도 세션으로 설계한다.

## 1. 전제

대화에서 이미 정한 것이다. 이 문서는 여기서 출발한다.

| 전제 | 내용 |
|---|---|
| 저장소 | 파이프라인과 Post BE 가 공유하는 MongoDB(D-43). MariaDB(인증)나 ES(색인)가 아니다 |
| 쓰는 주인 | 파이프라인(이 서비스) 하나. Post BE(`vans_story_be_post`)는 바꾸지 않는다. 이 서비스가 읽기 API 를 맡고 프런트(`vans_story`)가 표시한다 |
| 사전 단위 | 단어가 아니라 **뜻**이다. 표제어(표기·별칭)와 뜻(영역·정의·구분 문구) 두 층 |
| 등록 여부 | 코드가 정규화 키·별칭으로 먼저 확인하고, 애매한 것만 AI 가 제시된 후보 안에서 판정한다. 새 표제어·별칭·뜻은 사람 승인 뒤에만 표시한다 |
| ES | 지금은 쓰지 않는다. 용어 검색이 필요해지면 Mongo 에서 파생한 색인으로만 붙인다(2.9절) |
| 실행 모델 | 04 문서 그대로. 새 호출도 글 코루틴이 `_step` 한 걸음으로 하고 한 번 저장한다. 실패해도 발행을 막지 않는 단계는 다듬기(04 문서 5절)와 같은 정책이다 |

범위 밖: 사람이 에디터로 쓴 글의 용어 추출(Q-22), 관리자 화면(CLI 가 먼저다, Q-10 과 같은 판단), 전역 `/glossary` 페이지(2단계, 2.8절).

## 2. 용어 사전

### 2.1 데이터 모형

세 컬렉션이다. 표제어와 뜻은 전역 사전이고, 글→뜻 매핑은 글마다 하나다. 글 코루틴이 추출 때 남기는 결과는 `PostDraft` 에도 내장한다(발행 전에는 블로그 게시글 id 가 없기 때문이다).

#### 정규화 키

```python
def normalize_key(text: str) -> str:
    """NFKC → casefold → 공백·하이픈·밑줄·가운뎃점 제거. 영어·한국어 모두 같은 규칙이다."""
```

| 입력 | 키 |
|---|---|
| `Optimistic Locking`, `optimistic-locking` | `optimisticlocking` |
| `낙관적 잠금` | `낙관적잠금` |
| `Stateless`, `stateless` | `stateless` |
| `스테이트리스` | `스테이트리스` |

표제어 키(`headword_key`)는 `term_en` 의 키이고, `term_en` 이 없으면 `term_ko` 의 키다. 뜻 키(`sense_key`)는 **의미의 식별자**이고 단어도 영역도 담지 않는다. `glossary_counters` 의 순번으로 `s00042` 처럼 만들고 바꾸지 않는다.

| 경우 | 키 |
|---|---|
| 같은 단어, 다른 의미 ("토큰": 인증 서명 문자열 / 언어 모델 입력 단위) | 표제어 하나(`token`) 아래 뜻 키 둘 |
| 다른 단어, 같은 의미 ("무상태" / "stateless" / "스테이트리스") | 표제어 하나의 표기·별칭이고 뜻 키 하나 |
| 다른 영역, 같은 의미 ("트랜잭션"을 DB 글과 백엔드 글이 같은 뜻으로 씀) | 뜻 키 하나. 영역은 뜻의 속성 `domains`(여럿 가능)이지 키의 일부가 아니다(2026-11-07 사용자 지적) |

영역 태그는 코드의 고정 표 `DOMAINS`(`models/glossary.py`: `db` → 데이터베이스, `backend`, `frontend`, `auth`, `llm`, `network`, `os`, `devops`, `search`, `algorithm`, `lang`(언어·런타임), `other`)의 슬러그다. 표시 이름은 표에서 찾고 저장하지 않는다. AI 는 입력으로 받은 이 목록에서만 고르며, 표를 늘리는 것은 코드 변경이다(Q-30). 병합으로 표제어가 바뀌어도 뜻 키는 그대로이고 `headword_key` 필드만 고친다(2.4절).

#### 엔티티

```python
class GlossaryStatus(StrEnum):
    PROPOSED = "PROPOSED"   # 추출이 제안. 표시하지 않는다
    APPROVED = "APPROVED"   # 사람 승인. 표시한다
    REJECTED = "REJECTED"   # 사람 거절. 표시하지 않고, 같은 키의 재제안은 pending 으로 남는다
    MERGED = "MERGED"       # 표제어만. merged_into 를 따라간다


class GlossaryHeadword(BaseModel):
    """glossary_headwords 문서. 표기와 별칭. 뜻은 품지 않는다."""

    headword_key: str = Field(alias="_id")     # normalize_key(term_en or term_ko)
    term_ko: str                                # 표준 한국어 표기 (작성자 표기 표준의 원천)
    term_en: str | None = None                  # 영어 원어. 없으면 한국어만 있는 용어
    aliases: list[str] = []                     # 승인된 다른 표기 ("스테이트리스", "옵티미스틱 락")
    lookup_keys: list[str]                      # normalize_key(term_en, term_ko, *aliases). 정확 일치 조회와 unique 인덱스의 대상
    proposed_aliases: list[AliasProposal] = []  # 승인 전 별칭. 조회에 쓰지 않는다
    similar_headwords: list[str] = []           # 제안 때 코드가 찾은 비슷한 표제어 키(2.2절 ③). 사람 참고용
    status: GlossaryStatus
    merged_into: str | None = None
    proposed_by: ProposalSource | None = None   # 어느 글의 어느 호출이 제안했는지. CLI 로 만들면 None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AliasProposal(BaseModel):
    alias: str
    source: ProposalSource


class ProposalSource(BaseModel):
    draft_id: str
    call_id: str


class GlossarySense(BaseModel):
    """glossary_senses 문서. 사전의 단위."""

    sense_key: str = Field(alias="_id")        # 의미의 식별자. 순번 "s00042". 단어·영역을 담지 않는다
    headword_key: str
    domains: list[str] = Field(min_length=1)    # 영역 태그. DOMAINS 의 슬러그. 첫째가 주 영역. 표시 이름은 표에서 찾는다
    term_ko: str | None = None                  # 뜻마다 표기가 다를 때만 ("index": 검색은 색인, DB 는 인덱스). 없으면 표제어 값
    definition: str                             # 2문장 이하, "~다" 어미
    distinguisher: str = ""                     # 한 줄. "언어 모델 토큰이 아니라 인증용 서명 문자열". 표제어에 승인된 뜻이 둘 이상이면 필수
    status: GlossaryStatus
    post_ids: list[str] = []                    # 이 뜻을 가리키는 발행 글 (Post BE 게시글 id). 발행 때 코드가 채운다
    proposed_by: ProposalSource | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PostGlossaryItem(BaseModel):
    sense_key: str
    surface: str                                # 본문에 실제로 나온 표면형. "스테이트리스는". 프런트가 정확 일치로 찾아 감싼다
    section_id: str                             # 첫 등장 섹션 앵커
    resolution: Literal["EXACT", "SAME_AS", "NEW", "HUMAN"]   # 어떻게 뜻이 정해졌는지 (지표용)


class PendingTerm(BaseModel):
    """뜻을 정하지 못한 후보. 사람이 resolve 하거나 drop 한다. 표시하지 않는다."""

    term_ko: str
    term_en: str | None
    surface: str
    section_id: str
    headword_key: str | None                    # 표제어는 정해졌는데 뜻을 못 고른 경우
    sense_candidates: list[str]                 # AI 가 고른 후보 뜻 키 (코드가 그 표제어의 뜻인지 검증한 것만)
    similar_headwords: list[str]                # 코드가 n-gram 으로 찾은 비슷한 표제어 키 (사람 참고용)
    reason: str                                 # "뜻 미정", "새 표제어인데 뜻 제안 없음", "AI same_as 가 목록 밖"


class GlossaryRecord(BaseModel):
    """PostDraft.glossary. 추출 단계의 결과. 호출이 실패해도 남는다 (PolishRecord 와 같은 역할)."""

    target_version: int                         # 추출한 본문 버전 (발행본과 같아야 한다)
    call_id: str | None = None
    items: list[PostGlossaryItem] = []
    pending: list[PendingTerm] = []
    discarded: list[DiscardedTerm] = []         # surface 를 본문에서 못 찾음, 앵커 없음, prerequisites 에 있음 등
    skipped: str | None = None                  # 호출 실패 사유. 있으면 items 는 비어 있다
    known_headwords_sent: int                   # 입력에 넣은 표제어 수 (전체였는지 축소였는지, 2.3절)
    extracted_at: datetime


class PostGlossary(BaseModel):
    """post_glossary 문서. 발행 뒤 GlossaryRecord 를 게시글 id 로 옮긴 것."""

    post_id: str = Field(alias="_id")           # Post BE 게시글 id (= Publication.external_post_id)
    draft_id: str
    round_id: str
    content_version: int
    call_id: str | None
    prompt_template_version: str | None
    items: list[PostGlossaryItem]
    pending: list[PendingTerm]
    extracted_at: datetime
    updated_at: datetime                        # resolve·drop·보충 추출이 갱신
```

`DiscardedTerm` 은 `{surface, reason}` 이다. `PostGlossary` 와 `GlossaryRecord` 의 `items`·`pending` 은 같은 타입이다. 사람의 `resolve`·`drop` 은 `post_glossary` 에만 쓴다. `PostDraft.glossary` 는 추출 당시 기록이고 그 뒤 바꾸지 않는다.

#### 컬렉션·인덱스

| 컬렉션 | 문서 | 인덱스 |
|---|---|---|
| `glossary_headwords` | `GlossaryHeadword` | `_id`(표제어 키). `lookup_keys` **unique**(다중키): 한 키는 표제어 하나에만 속한다. `status` |
| `glossary_senses` | `GlossarySense` | `_id`(뜻 키). `headword_key`. `(status, headword_key)`. `post_ids` |
| `post_glossary` | `PostGlossary` | `_id`(게시글 id). `draft_id` unique. `items.sense_key` |
| `glossary_counters` | `{_id: "sense", seq: int}` | `_id` 만. `find_one_and_update($inc)` 로 뜻 키 순번을 받는다 |

`lookup_keys` 의 unique 다중키 인덱스가 "별칭은 표제어 하나에만 속한다"를 DB 에서 강제한다. 같은 문서 안의 중복은 허용되고 문서 사이에서만 막힌다. 새 표제어를 `insert` 할 때 `DuplicateKeyError` 가 나면 그 키를 가진 표제어가 이미 있다는 뜻이라 정확 일치로 다시 찾는다(2.2절 ②).

#### 불변식

| 불변식 | 강제하는 곳 |
|---|---|
| `headword_key == normalize_key(term_en or term_ko)` | 생성 코드. 사람이 `term_en` 을 고치면 키는 그대로이고 `lookup_keys` 만 바뀐다(키는 식별자다) |
| `lookup_keys` ⊇ `{normalize_key(term_ko), normalize_key(term_en)}` ∪ `normalize_key(aliases)` | 저장 전에 코드가 다시 계산한다 |
| 한 `lookup_key` 는 표제어 하나에만 | unique 인덱스 |
| `sense.headword_key` 는 있는 표제어이고 `MERGED` 가 아니다 | 병합이 뜻의 `headword_key` 를 함께 옮긴다. `agents glossary check` 가 어긋남을 찾는다 |
| 뜻 하나는 의미 하나다. 영역이 다른 글이 같은 정의로 쓰면 같은 뜻이고, 영역은 `domains` 에 더한다 | 추출 프롬프트(2.3절)와 사람 승인. 코드는 새 뜻 제안을 `PROPOSED` 로만 두고 `pending` 이 기존 뜻을 나란히 보인다 |
| `sense.domains` 는 비어 있지 않고 모두 `DOMAINS` 의 슬러그다 | 파서가 모르는 슬러그를 버리고, 남는 것이 없으면 `other` 다 |
| 표제어에 `APPROVED` 뜻이 둘 이상이면 그 모든 뜻에 `distinguisher` 가 있다 | 뜻 승인 때 검사. 둘째 뜻을 승인하려면 첫째 뜻에도 구분 문구를 넣어야 한다 |
| `PostGlossary.items` 의 `sense_key` 는 글 하나 안에서 중복이 없고, `surface` 도 중복이 없다 | 추출 코드와 `resolve` |
| `surface` 는 그 버전 본문에 코드 펜스·인라인 코드 밖에서 그대로 나오고, 줄바꿈이 없고, `GLOSSARY_SURFACE_MAX_CHARS` 이하다 | 추출 코드(못 찾으면 `discarded`) |
| 표시되는 항목은 뜻과 그 표제어가 모두 `APPROVED` 다 | 읽기 API 가 거른다(2.6절). 저장은 상태와 무관하게 한다 |

### 2.2 흐름

```
추출(GLOSSARY 호출) → 등록 확인(코드) → 뜻 선택(코드·AI) → 제안 저장(PROPOSED) → [발행: post_glossary] → 승인(CLI) → 표시(읽기 API·프런트)
```

#### 단계 위치

`POST_REVIEW` 에 넷째 단계 `GLOSSARY` 를 더한다. 순서는 다듬기 → 전체 읽기 → 수준 평가 → **용어 추출** → `READY_TO_PUBLISH` 다. 최종 본문이 확정된 뒤여야 `surface` 가 발행본과 같고, 발행 전이어야 글 코루틴의 `_step` 한 걸음으로 끝난다.

| 단계 | 모델 | 내용 | 실패 |
|---|---|---|---|
| 용어 추출 | `GLOSSARY`(저가 모델, haiku 가족. 벤더 제약 없음) | 발행본에서 전제 밖 용어 후보를 뽑고 기존 표제어·뜻과 맞춘다. 결과는 `PostDraft.glossary` | 건너뛴다. `GlossaryRecord.skipped` 에 사유를 남기고 `READY_TO_PUBLISH` 로 간다. 글을 멈추지 않는다 |

- 진입은 `POST_REVIEW` 를 거치는 글뿐이다. 사람 재수정(`origin_cause=HUMAN`)과 `PUBLISH_AS_IS` 는 `POST_REVIEW` 를 거치지 않으므로(D-17, D-22) 추출이 없다. 보충 명령 `agents glossary extract` 가 채운다(2.5절).
- `GLOSSARY` 는 **선택 라우트**다(`select_optional`). 스냅샷에 없는 라운드(배포된 `agent_routes` 에 아직 없거나 이 기능 전에 만든 라운드)는 호출하지 않고 `skipped="no GLOSSARY route"` 로 지나간다. `validate_snapshot` 은 있을 때만 확인하고 없어도 라운드를 만든다. D-51 의 "빠진 설정을 숨기지 않는다"와 달리 둔 이유는 배포된 라우트 문서에 뒤늦게 더해지는 라우트가 라운드 생성을 막으면 안 되기 때문이다(D-64).
- 다듬기와 마찬가지로 호출 실패(재시도 소진·예산 초과·잘림·벤더 거부)는 전부 건너뛴다. `post_review_stage` 와 `glossary` 를 한 번에 저장해 재개 때 되풀이하지 않는다(D-49).
- 예산은 글 소프트 상한에 든다. haiku 호출 한 번이라 작다.

```
DraftPipelineService._post_review:
    case PostReviewStage.GLOSSARY:
        await self._glossary.extract(draft, publishing_round)   # GlossaryService. 돌아오면 post_review_stage == DONE, status == READY_TO_PUBLISH
```

#### 등록 확인 순서 (후보마다, 코드가 한다)

| 순서 | 검사 | 결과 |
|---|---|---|
| ① 정확 일치 | `normalize_key(term_en)` 과 `normalize_key(term_ko)` 를 `lookup_keys` 에서 찾는다. `surface` 는 조사가 붙어 있어 조회에 쓰지 않는다. `MERGED` 면 `merged_into` 를 따라간다 | 찾으면 그 표제어. AI 의 `same_as` 가 다른 키를 가리켜도 코드가 이긴다(로그) |
| ② AI 의 `same_as` | ①이 없고 `same_as` 가 입력 목록의 키면 그 표제어. `term_ko`·`term_en` 의 키가 그 표제어의 `lookup_keys` 에 없으면 `proposed_aliases` 에 별칭 제안을 넣는다(승인 전에는 조회에 쓰지 않는다) | 그 표제어 |
| ③ 비슷한 표제어 | ①②가 없으면 코드가 문자 2-gram Jaccard(키 기준)와 편집 거리로 후보 5~10개를 뽑는다. 사전이 `GLOSSARY_FULL_LIST_MAX`(기본 300) 이하면 AI 는 이미 전체 목록을 봤으므로 이 후보는 **사람 참고용**(`similar_headwords`)이다. 사전이 그보다 크면 2단계 호출(`GLOSSARY_MATCH`, 2.3절)에 이 후보만 보여 ②를 다시 받는다 | 후보 목록 |
| ④ 새 표제어 | ①②③ 모두 없으면 `PROPOSED` 표제어를 `_id = headword_key` 로 `insert` 한다(`similar_headwords` 에 ③의 후보를 남긴다). `DuplicateKeyError` 면 ①로 돌아간다(동시 추출한 다른 글이 먼저 만들었다) | 새 표제어(승인 전) |

#### 뜻 선택

| 상황 | 결정 |
|---|---|
| AI 가 `sense_key` 를 줬고 그 키가 정해진 표제어의 뜻이다 | 그 뜻. `resolution = EXACT`(①) 또는 `SAME_AS`(②) |
| AI 가 `new_sense` 를 제안했다 | 새 순번으로 `PROPOSED` 뜻을 만든다. "영역만 다른" 제안을 코드가 가려낼 수는 없으므로 `pending`·`show` 가 같은 표제어의 기존 뜻을 나란히 보이고, 사람이 승인 대신 `merge-sense` 로 합친다. 같은 글·같은 표제어에 같은 정의가 둘이면 하나만 남긴다. `resolution = NEW` |
| `sense_key` 도 `new_sense` 도 없고 표제어의 뜻이 하나다 | 그 뜻 |
| `sense_key` 도 `new_sense` 도 없고 뜻이 둘 이상이다 | `pending`(`sense_candidates` 는 AI 가 준 후보 중 그 표제어의 뜻만) |
| 새 표제어인데 `new_sense` 가 없다 | `pending`("새 표제어인데 뜻 제안 없음"). 표제어 제안은 남긴다 |
| AI 의 `sense_key` 가 다른 표제어의 뜻이다 | 무시하고 위 규칙을 다시 적용 |

새 뜻은 "기존 정의가 이 글에서 틀린 말이 될 때만"이다. 영역이 다른 것은 이유가 아니다("트랜잭션"은 DB 글에서나 백엔드 글에서나 같은 뜻이다). 이 규칙은 프롬프트에 있고, 코드는 새 뜻 제안을 항상 `PROPOSED` 로만 둔다. 지어낸 뜻이나 영역만 다른 중복이 표시되는 일은 승인 단계가 막는다.

#### 승인 상태 전이

| 대상 | 전이 | 누가 |
|---|---|---|
| 표제어 | `PROPOSED → APPROVED` (`term_ko`·`term_en` 을 고쳐 승인할 수 있다), `PROPOSED → REJECTED`, `APPROVED → MERGED`(병합) | CLI(`agents glossary …`, 2.5절) |
| 별칭 | `proposed_aliases` 항목 → `aliases` + `lookup_keys` (승인) 또는 삭제(거절). 다른 표제어가 그 키를 이미 가지면 unique 인덱스가 거절한다 | CLI |
| 뜻 | `PROPOSED → APPROVED` (정의·구분 문구를 고쳐 승인할 수 있다), `PROPOSED → REJECTED`, `APPROVED → REJECTED`(철회) | CLI |
| 글 항목 | `pending` → `items`(`resolve`, `resolution = HUMAN`), `pending`·`items` → 삭제(`drop`) | CLI |

승인은 전역이다. 뜻 하나를 승인하면 그 뜻을 가리키는 모든 글에서 바로 보인다(프런트 캐시 TTL 뒤). 글마다 다시 승인하지 않는다.

#### 발행 연결

발행 성공(04 문서 1절 표 "발행 성공" 행)의 따라 쓰기에 둘을 더한다. 커밋 지점은 그대로 글 `PUBLISHED` 다.

```
★ 글 PUBLISHED + Publication (조건 status==READY_TO_PUBLISH)
→ 레지스트리 PUBLISHED
→ post_glossary $setOnInsert(_id = external_post_id)     # 글에 glossary 가 있을 때만
→ glossary_senses.post_ids $addToSet(post_id)            # items 의 뜻마다
→ post_provenance $setOnInsert(_id = external_post_id)   # 3절
→ 글 publication.derived_at = now                        # 파생 문서를 다 썼다는 표시. revision 을 올리지 않는 단일 필드 $set
```

중간에 멈추면 `RoundService.reconcile` 이 `PUBLISHED` 이고 `publication.derived_at` 이 없는 글을 찾아 마저 쓴다. 모두 결정론적 ID 의 `$setOnInsert`·`$addToSet` 이라 되풀이해도 결과가 같다. `CONFIRM_PUBLISHED`(사람이 블로그에서 찾은 id) 경로도 같은 따라 쓰기를 한다. 정리는 LLM 을 부르지 않는다. 추출이 없는 글(사람 경로, 옛 글)의 `post_glossary` 는 보충 명령만 만든다.

#### 표시 규칙

- 툴팁의 맨 위는 **이 글에서 쓰인 뜻**이다: `term_ko (term_en)`, 영역 라벨(`domains` 의 표시 이름. 여럿이면 가운뎃점으로 잇는다), 정의. 표제어에 승인된 뜻이 둘 이상이면 영역 라벨 옆에 `distinguisher` 를 붙인다.
- 그 아래 "다른 영역에서는"으로 같은 표제어의 **다른 승인된 뜻**을 영역 라벨 + 정의 한 줄씩 보인다. 뜻이 하나뿐이면 이 부분이 없다. 뜻 하나만 보일 거면 각주로 족하고, 툴팁의 값은 이 글의 뜻이 어느 영역의 것이고 다른 곳에서는 어떻게 쓰이는지를 함께 보이는 데 있다는 사용자 판단(2026-11-07)이다.
- 프런트는 `surface` 의 **첫 등장**만 감싼다. 코드 블록·인라인 코드 안은 건너뛴다. 못 찾으면 감싸지 않고 글 끝 "용어" 블록에만 나온다.
- 글 끝 "용어" 블록은 그 글의 승인된 항목 전부를 `term_ko (term_en)` — 영역 라벨 — 정의 순으로 접힌 채 보인다. 다른 영역의 뜻은 개수만 적고, 2단계에서 `/glossary#<headword_key>` 로 잇는다.

### 2.3 호출 계약 `GLOSSARY`

- 라우트: `agent_routes` 에 `{"_id": "GLOSSARY", "candidates": [{"provider": "anthropic", "model_id": "claude-haiku-5-5", "effort": "low", "max_output_tokens": 8000}]}`(`routes.example.json` 에 있다). 선택은 `select_optional`(벤더 제약 없음, 없으면 `None`). `validate_snapshot` 은 있을 때만 확인한다. 배포된 Mongo 에는 `agents seed-routes` 로 넣기 전까지 없다.
- 템플릿: `app/agents/prompts/v4/haiku/glossary.md`(`fact_transform.md` 와 같은 자리)와 **같은 파일을 `v3/glossary.md`(평면)에도 둔다**. 로더는 활성 버전 디렉터리만 보므로 기본 `prompt_version` 이 v3 인 동안에도 찾아야 한다. v3 에 새 이름을 더하는 것은 기존 호출의 문구를 바꾸지 않는 추가다(D-69, Q-25 해소). `TEMPLATE_NAMES` 에 `glossary`, `_TEMPLATES_BY_ROUTE["GLOSSARY"] = ("glossary",)`. 영어 지시문·한국어 결과물(v4 규칙). 가짜 LLM 은 문구 "glossary extractor"로 종류를 가린다.
- 타임아웃은 `review_timeout_s`. 예산 검사는 글 호출과 같다.
- 입력 축소: 승인·제안 표제어 수가 `GLOSSARY_FULL_LIST_MAX` 이하면 전체를 보낸다. 넘으면 2단계 호출로 나눈다. 1차 `GLOSSARY`(목록 없이 후보만 추출) → 코드 ①③ → 2차 `GLOSSARY_MATCH`(후보마다 비슷한 표제어 5~10개와 그 뜻만 보여 `same_as`·`sense_key` 판정). 2차 호출은 사전이 커진 뒤의 일이고 계약은 아래와 같은 모델을 쓴다(Q-20).

입력:

```python
class DomainOption(BaseModel):
    slug: str
    label: str


class KnownSense(BaseModel):
    sense_key: str
    domains: list[str]
    definition: str
    distinguisher: str
    status: GlossaryStatus              # APPROVED·PROPOSED 만 보낸다. REJECTED 는 보내지 않는다


class KnownHeadword(BaseModel):
    key: str
    term_ko: str
    term_en: str | None
    aliases: list[str]
    senses: list[KnownSense]


class GlossaryInput(BaseModel):
    title: str
    audience: str                       # topic.audience
    prerequisites: str                  # topic.prerequisites. 여기 있는 지식은 뽑지 않는다
    main_category: str                  # 라운드 categories 의 main_label
    keywords: list[str]
    content_markdown: str               # 발행본. 앵커 유지 (section_id 의 근거)
    section_ids: list[str]
    known_headwords: list[KnownHeadword]   # 전체 또는 축소 목록 (2.3절 입력 축소)
    allowed_domains: list[DomainOption]    # DOMAINS 표. 새 뜻의 domains 는 이 중에서만
    min_terms: int = 8
    max_terms: int = 12
```

출력:

```python
class SenseProposal(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=3)   # 입력 allowed_domains 의 슬러그만. 첫째가 주 영역
    definition: str = Field(max_length=240)     # 2문장 이하, "~다"
    distinguisher: str = Field(max_length=80)   # 한 줄. 표제어에 다른 뜻이 있을 때 채운다


class GlossaryCandidate(BaseModel):
    term_ko: str
    term_en: str | None
    surface: str                        # 본문에 있는 그대로. 조사 포함. 코드 밖. 한 줄
    section_id: str                     # 첫 등장 앵커
    same_as: str | None                 # known_headwords 의 key 중 하나 또는 null
    sense_key: str | None               # 그 표제어의 기존 뜻 키. 확신이 없으면 null
    sense_candidates: list[str] = []    # sense_key 가 null 일 때 가능한 뜻 키
    new_sense: SenseProposal | None = None   # 기존 정의가 이 글에서 틀린 말이 될 때만


class GlossaryOutput(BaseModel):
    candidates: list[GlossaryCandidate] = Field(max_length=12)
```

후보 검사는 `agents/parsers.py` 가 아니라 `services/glossary.py`(`_discard_reason` 과 등록 확인)가 한다. 등록 확인이 DB 조회를 포함해 입력 문맥만 보는 파서에 둘 수 없기 때문이다(D-65). 스키마 위반만 재시도이고, 나머지는 항목 단위로 버려 `discarded` 에 남긴다. 싸고 막지 않는 호출이라 SPOT_CHECK 의 "버리고 로그" 방식을 따른다.

| 검사 | 처리 |
|---|---|
| `section_id` 가 앵커 목록에 없다(`normalize_section_id` 뒤) | 버린다 |
| `surface` 가 본문의 펜스·인라인 코드 밖에 없다, 줄바꿈이 있다, `GLOSSARY_SURFACE_MAX_CHARS` 를 넘는다 | 버린다 |
| `same_as` 가 입력 목록에 없다 | `None` 으로 본다(등록 확인 ③④) |
| `sense_key`·`sense_candidates` 가 정해진 표제어의 뜻이 아니다 | 그 키만 뺀다 |
| 후보끼리 `surface` 또는 (표제어·뜻)이 겹친다 | 먼저 나온 것만 남긴다 |
| `term_ko`·`term_en` 모두 비었다 | 버린다 |
| `new_sense.domains` 에 `allowed_domains` 밖 슬러그 | 그 슬러그만 뺀다. 남는 것이 없으면 `other` |
| `prerequisites` 의 문장에 `term_ko` 나 `term_en` 이 글자 그대로 들어 있다 | 버린다(프롬프트도 금하지만 코드가 다시 거른다) |

프롬프트 수준 규칙(05 문서 7절 가드레일에 해당):

| 규칙 | 내용 |
|---|---|
| 선정 기준 | 본문이 쓰지만 정의하지 않고 `prerequisites` 에 없는 용어. 독자(`audience`)가 모를 만한 것. 글당 8~12개 |
| 제외 | 이 글이 새로 만든 이름(변수·함수·파일·클래스), 고유명사가 아닌 일반어, 이미 본문이 정의한 용어 |
| 정의 | 일반적으로 통용되는 뜻만. 확신이 없으면 후보에서 뺀다. 2문장 이하, "~다" 어미, 이 글의 맥락이 아니라 일반 정의 |
| 표기 | `term_ko` 는 통용 표기(`무상태`), `surface` 는 본문 그대로(`스테이트리스는`). 둘이 다르면 그것이 별칭 제안이 된다 |
| 목록 우선 | `known_headwords` 에 같은 개념이 있으면 반드시 `same_as` 로 가리킨다. 목록 밖 키를 만들지 않는다 |
| 새 뜻 | 기존 뜻의 정의가 이 글에서 틀린 말이 될 때만. 영역이 다르다는 것은 이유가 아니다("트랜잭션"은 어디서나 같은 뜻). 같은 뜻을 말만 바꿔 다시 제안하지 않는다 |

### 2.4 정리 작업 (병합 제안)

사전이 자라면 같은 개념이 다른 표제어로 들어온다("optimistic locking" 과 "낙관적 잠금"이 서로 모르고 생긴 경우). 주기적으로 사람이 돌린다.

| 단계 | 내용 |
|---|---|
| 제안 | `agents glossary dedupe [--out]`: 코드가 조회 키의 2-gram 유사도로 쌍 후보(`keep`, `merge`, `score`, 표기)를 찍는다. DB 는 바꾸지 않는다. 문자가 다른 같은 개념("낙관적 잠금"/"optimistic locking")은 코드가 못 잡으므로 사람이 `merge` 를 고른다. 표제어 전체를 LLM 에 보여 `merge_pairs` 를 받는 판정은 사전이 몇백 개가 된 뒤의 2단계다(Q-31) |
| 적용 | `agents glossary merge <merge_key> --into <keep_key>`: ① 흡수되는 표제어의 `lookup_keys` 를 비운다 ② 그 키와 별칭을 남는 표제어의 `aliases`·`lookup_keys` 에 더한다 ③ 흡수되는 표제어의 뜻들의 `headword_key` 를 남는 키로 바꾼다 ④ 흡수되는 표제어를 `MERGED(merged_into)` 로 둔다. ①②가 unique 인덱스 때문에 두 쓰기다. 사이에 죽으면 키가 잠시 어느 표제어에도 없고, `agents glossary check` 가 `MERGED` 인데 `lookup_keys` 가 옮겨지지 않은 표제어를 찾아 ②부터 다시 한다 |
| 뜻 병합 | 같은 표제어 아래 같은 뜻이 둘이면 `agents glossary merge-sense <from> --into <to>`: `post_glossary.items.sense_key` 를 `updateMany` 로 바꾸고 `post_ids` 를 합친 뒤 `from` 을 `REJECTED` 로 둔다 |
| 무결성 | `agents glossary check`: 고아 키, `MERGED` 미완, 뜻이 가리키는 표제어 없음, 둘 이상 승인된 뜻에 구분 문구 없음, `post_glossary` 가 가리키는 뜻 없음 |

### 2.5 승인·운영 CLI

초안 승인(`agents approve`·`resolve`)과 같은 결이다. 서비스(`GlossaryService`)만 부르고 리포지토리를 직접 쓰지 않는다. 출력은 JSON.

| 명령 | 동작 |
|---|---|
| `agents glossary pending [--draft-id] [--post-id]` | `PROPOSED` 표제어·뜻·별칭과 `pending` 항목을 글별로 묶어 찍는다. 라운드가 끝난 뒤 사람이 읽는 목록이다 |
| `agents glossary show <post_id \| draft_id>` | 그 글의 항목(표시 여부 포함)·보류·버린 것 |
| `agents glossary approve <post_id \| draft_id>` | 그 글이 제안한 표제어·뜻·별칭을 한 번에 승인한다. 읽고 나서 쓰는 지름길. 구분 문구 불변식에 걸리면 그 뜻만 남기고 사유를 찍는다 |
| `agents glossary approve-headword <key> [--term-ko] [--term-en]` | 표기를 고쳐 승인 |
| `agents glossary approve-sense <sense_key> [--definition] [--distinguisher] [--domains db,backend] [--term-ko]` | 정의·영역을 고쳐 승인. 표제어가 `PROPOSED` 면 함께 승인한다 |
| `agents glossary reject-headword <key>` / `reject-sense <sense_key>` | 거절. 그 뜻을 가리키던 항목은 표시되지 않는다 |
| `agents glossary alias <key> <alias>` / `unalias <key> <alias>` | 별칭 승인·삭제. `proposed_aliases` 에 있으면 거기서 옮긴다 |
| `agents glossary resolve <post_id> --surface "…" --sense <sense_key>` | 보류 항목에 뜻을 붙인다(`HUMAN`) |
| `agents glossary drop <post_id> --surface "…"` | 항목·보류를 지운다 |
| `agents glossary extract <draft_id> [--missing]` | 보충. 발행본으로 `GLOSSARY` 호출을 돌려 `PostDraft.glossary` 를 쓰고(있으면 덮어쓴다), `PUBLISHED` 면 `post_glossary` 를 다시 만든다(사람의 `resolve`·`drop` 은 사라진다. 실행 전에 경고). `--missing` 은 `PUBLISHED` 인데 `glossary` 가 없는 글 전부 |
| `agents glossary dedupe [--out <file>]` / `merge` / `merge-sense` / `check` | 2.4절 |
| `agents glossary terms [--status] [--q]` | 사전을 찍는다 |

API 로 같은 결정을 받는 관리 엔드포인트(`/api/v1/agents/glossary/…`, `X-Internal-Key`)는 관리 화면을 만들 때 더한다(Q-10 과 같은 판단).

### 2.6 읽기 API

프런트가 읽는다. **키 없이 공개**한다(발행된 글의 부속이라 비밀이 없다). 기존 `routers/search.py` 처럼 `APIRouter(prefix="/api/v1/glossary", tags=["Glossary"])` 이고 `async def` 다. 응답 스키마는 `schemas/glossary.py`, 변환은 `services/glossary_views.py`(`PipelineViewService` 와 같은 역할). 없는 id 는 404.

| 메서드 | 경로 | 동작 |
|---|---|---|
| `GET` | `/api/v1/glossary/posts/{post_id}/` | 그 글의 **승인된** 항목. 항목마다 이 글의 뜻과 같은 표제어의 다른 승인된 뜻(`other_senses`)을 함께 준다(툴팁이 조회 없이 그린다). `post_glossary` 가 없으면 404(사람 글). 있는데 승인된 항목이 없으면 200 과 빈 목록(프런트가 블록을 숨긴다) |
| `GET` | `/api/v1/glossary/terms/?q=&page=&size=` | 전역 사전. `APPROVED` 표제어와 그 아래 `APPROVED` 뜻(번호는 `created_at` 순), 뜻마다 글 목록. `q` 는 `normalize_key(q)` 의 `lookup_keys` 부분 일치(정규식). ES 없이 Mongo 만 |
| `GET` | `/api/v1/glossary/headwords/{key}/` | 표제어 하나와 승인된 뜻 전부, 뜻마다 글 목록. 키 기반 툴팁이 글 페이지 밖에서 쓰는 조회 경로이고 2단계 `/glossary#<key>` 딥링크용이다 |

```python
class DomainPublic(BaseModel):
    slug: str
    label: str                          # DOMAINS 표의 표시 이름


class OtherSensePublic(BaseModel):
    """같은 표제어의 다른 승인된 뜻. 툴팁 아래쪽 "다른 영역에서는" 목록."""

    sense_key: str
    domains: list[DomainPublic]
    definition: str


class PostGlossaryItemPublic(BaseModel):
    sense_key: str
    headword_key: str
    surface: str
    section_id: str
    term_ko: str                        # 뜻의 term_ko 가 있으면 그것, 없으면 표제어 값
    term_en: str | None
    domains: list[DomainPublic]         # 첫째가 주 영역
    definition: str
    distinguisher: str                  # 다른 뜻이 있을 때 영역 라벨 옆에 보인다
    other_senses: list[OtherSensePublic]   # 비어 있으면 뜻이 하나다


class PostGlossaryPublic(BaseModel):
    post_id: str
    items: list[PostGlossaryItemPublic]
    extracted_at: datetime


class PostRef(BaseModel):
    post_id: str
    title: str
    main_category: str                  # /post/view/{main_category}/{post_id} 링크용


class SensePublic(BaseModel):
    sense_key: str
    number: int                         # 표제어 안 순번
    domains: list[DomainPublic]
    term_ko: str
    definition: str
    distinguisher: str
    posts: list[PostRef]


class HeadwordPublic(BaseModel):
    key: str
    term_ko: str
    term_en: str | None
    aliases: list[str]
    senses: list[SensePublic]


class GlossaryTermList(BaseModel):
    items: list[HeadwordPublic]
    total: int
```

`PostRef` 의 제목·메인 카테고리는 Post BE 가 소유한 `posts`·`categories` 컬렉션을 **읽기만** 해서 채운다(동기화 서비스의 `PostRepository` 가 이미 같은 컬렉션을 읽는다. 비동기 클라이언트로 같은 조회를 하는 `repositories/post_ref.py` 를 둔다). 삭제된 글은 뺀다. 응답 골든은 `tests/golden/glossary_post.json`·`glossary_terms.json`·`glossary_headword.json`.

### 2.7 작성자 표기 표준 경로

승인된 사전은 작성자에게 "이 표기를 써라"의 원천이다. 사전이 비어 있을 때는 아무것도 바뀌지 않는다.

| 항목 | 내용 |
|---|---|
| 입력 모델 | `TerminologyEntry(term_ko, term_en, avoid: list[str])` 목록. `avoid` 는 `aliases`(승인된 비표준 표기). `ProposeTopicInput`·`WriteDraftInput`·`ReviseDraftInput`·`PolishToneInput` 에 `terminology: list[TerminologyEntry] = []` 를 더한다 |
| 만드는 곳 | `GlossaryService.terminology_for(main_category, keywords, limit)`: `APPROVED` 표제어 중 `post_ids` 가 많은 순. `GLOSSARY_TERMINOLOGY_MAX`(기본 150)를 넘으면 글의 메인 카테고리에서 쓰인 뜻의 표제어를 먼저 둔다. 라운드 생성 때 한 번 읽어 `PublishingRound.terminology` 스냅샷으로 둔다(라우트와 같은 이유: 글 중간에 바뀌지 않는다) |
| 템플릿 | 지시 한 단락: "`terminology` 의 `term_ko` 를 쓰고 `avoid` 의 표기는 쓰지 않는다. 처음 나올 때 `무상태(stateless)` 처럼 원어를 괄호에 적는다". 배포된 버전 디렉터리는 고치지 않으므로 v4 가 배포 전이면 v4 에, 아니면 v5 에 넣는다(05 문서 머리말) |
| 다듬기 | 정규식 단계에서 승인된 별칭 → `term_ko` 치환은 `_preserved_violation`(라틴 토큰 순서 보존)에 걸리지 않는 한국어 ↔ 한국어 치환만 가능하다. 넣을지는 Q-23 |
| 지표 | 발행본에서 `avoid` 표기가 나온 횟수(07 문서 4절에 더한다). 표준이 작동하는지 본다 |

### 2.8 프런트 (vans_story)

조사한 현재 구조(2026-11-07, `D:/blog/vans_story`):

| 무엇 | 경로 |
|---|---|
| 글 상세 페이지 | `src/app/post/view/[mainCategory]/[id]/page.tsx`(서버 컴포넌트). `getPost = cache(id => getPostWithViewCount(id))` 로 Post BE 를 서버에서 직접 부른다(`src/lib/posts/clientActions.ts`, `ApiFetch.basicGet`) |
| 본문 체인 | `PostLayout`(`src/components/features/post/view/PostLayout.tsx`) → `PostDetail`(`…/view/PostDetail.tsx`, 서버 컴포넌트: 메타 줄 → `<Viewer content={post.content} />` → 태그) |
| 렌더러 | `src/components/features/post/viewer/Viewer.tsx`: `useEditor({ extensions: EditorExtensions, content, editable: false, immediatelyRender: false })` + `<EditorContent>`. 읽기 전용 TipTap 인스턴스다. `generateHTML`·`dangerouslySetInnerHTML` 은 쓰지 않는다 |
| 확장 목록 | `src/components/features/post/editor/EditorExtensions.tsx`(StarterKit, Link, CodeBlockLowlight, Table, AiDiffBlock 등). 에디터와 뷰어가 같은 배열을 쓴다 |
| BFF | `src/app/api/**/route.ts`. FastAPI 프록시는 `src/lib/api/apiProxy.ts` 의 `proxyGet({ baseUrl, path, revalidate, additionalHeaders })`. FastAPI 주소는 `SEARCH_API_URL`(`src/constants/apiConfig.ts`). 예: `src/app/api/search/route.ts`, `src/app/api/code-graph/route.ts` |
| 툴팁 | `src/components/features/stocks/detail/TermTooltip.tsx`: `{ children, text, width? }`. `onMouseEnter/Move`·`onFocus` 로 커서·요소 위치에 `fixed` 패널. 포털·팝오버 라이브러리 없음. 클릭·탭 토글 없음. 트리거 span 이 `display: contents` 라 스스로 포커스를 받지 못한다 |
| 접기 | 공용 컴포넌트 없음. `src/components/features/projects/code-graph/DiagnosticsPanel.tsx` 의 `DiagnosticSection`(네이티브 `<details>/<summary>`)이 가장 가깝다 |
| i18n | `src/lib/i18n.ts` 의 `useTranslation()`(클라이언트 전용, Redux `i18n` 슬라이스). 사전은 `src/messages/{ko,en}/post.json` |
| 요약 노트 | "## 이 글은 어떻게 만들어졌나"에 특별 처리 없음. 일반 heading·paragraph 로 그린다 |

#### 설계

| 항목 | 내용 |
|---|---|
| 서버 조회 | `PostDetail` 이 `getPostGlossary(id)`·`getPostProvenance(id)`(`src/lib/agents/serverActions.ts`, 새 파일)를 `Promise.all` 로 부른다. `SEARCH_API_URL` 을 서버에서 직접 호출(`getPostWithViewCount` 가 Post BE 를 부르는 것과 같은 방식), `next: { revalidate: API_CONFIG.CACHE.GLOSSARY }`(300초). 404 → `null`, 오류 → `null` + `console.warn`. **글은 항상 그린다** |
| BFF 라우트 | `src/app/api/glossary/posts/[id]/route.ts`, `src/app/api/glossary/headwords/[key]/route.ts`, `src/app/api/glossary/terms/route.ts`, `src/app/api/agents/posts/[id]/provenance/route.ts`. 모두 `proxyGet`, 인증 없음, 캐시 300초. 서버 컴포넌트는 직접 호출하고 이 라우트는 클라이언트 조회(2단계 `/glossary` 페이지, 지연 로드)용이다. 환경 변수는 새로 없다(`SEARCH_API_URL` 재사용) |
| 본문 주석 달기 | `src/lib/glossary/annotate.ts` 의 순수 함수 `annotateGlossary(content, items)`: TipTap JSON 을 걷되 `codeBlock` 안과 `code` 마크가 있는 텍스트 노드는 건너뛰고, 항목마다 **문서 순서의 첫** 텍스트 노드에서 `surface` 를 찾아 그 텍스트 노드를 `[앞][glossaryTerm 노드][뒤]` 로 쪼갠다. 앞·뒤와 노드 안 텍스트는 원래 마크를 유지한다. 한 텍스트 노드 안에서만 찾는다(굵게 경계를 가로지르는 표면형은 감싸지 않는다). 못 찾은 항목은 그대로 두고 `missing` 목록으로 돌려준다. 저장하지 않고 보일 때마다 만든다. 단위 테스트는 vitest(첫 등장만, 코드 제외, 마크 보존, 못 찾음) |
| 인라인 노드 | `src/components/features/post/viewer/GlossaryTerm.tsx`: TipTap `Node.create({ name: 'glossaryTerm', group: 'inline', inline: true, content: 'text*', attrs: { senseKey } })`. `renderHTML` 은 `['span', { 'data-sense-key', class: 'glossary-term' }, 0]`, `addNodeView` 는 `ReactNodeViewRenderer(GlossaryTermView)`. 뷰는 `<NodeViewWrapper as="span"><TermTooltip text={…} tapToggle><NodeViewContent as="span" /></TermTooltip></NodeViewWrapper>`. 툴팁 내용은 `useGlossaryTerm(senseKey)` 훅(`src/lib/glossary/useGlossaryTerm.ts`)이 준다: Viewer 가 글 단위 응답으로 채운 `GlossaryContext`(`senseKey → PostGlossaryItemPublic`)를 먼저 보고, 없으면(글 페이지 밖에서 같은 노드를 쓸 때) `headword_key` 로 `/api/glossary/headwords/{key}` 를 받아 키별로 캐시하고 받는 동안 "불러오는 중"을 보인다. 글 페이지에서는 조회가 없어 호버 지연이 없다. `Viewer` 만 `[...EditorExtensions, GlossaryTerm]` 을 쓴다. 에디터는 주석 달린 JSON 을 받을 일이 없다(저장하지 않는다) |
| `TermTooltip` 확장 | `tapToggle?: boolean` 과 `content?: ReactNode`(`text` 대신 구조가 있는 내용)를 더한다. 켜면 트리거를 `display: inline` span 으로 바꾸고 `tabIndex=0`·`role="button"`·`aria-describedby` 를 붙인다. 클릭(터치)은 열림을 토글하고, 열려 있는 동안 바깥 `pointerdown`·`Escape`·스크롤이 닫는다. 호버·포커스 동작은 그대로다. 기존 호출부(`FundamentalsBlock`·`PriceChart`·`FearGreedBar`)는 기본값(끔)이라 바뀌지 않는다. 용어 툴팁의 구성: ① `term_ko (term_en)` 굵게 + 영역 라벨(다른 뜻이 있으면 `distinguisher` 도) ② 이 글의 정의 ③ 다른 뜻이 있을 때만 구분선 아래 "다른 영역에서는" + 영역 라벨·정의 한 줄씩. 색은 쓰지 않고 영역 라벨은 괄호나 작은 글자다. `width` 는 280 |
| 글 끝 "용어" 블록 | `src/components/features/post/view/PostGlossary.tsx`(클라이언트, i18n): `<details>` 접힌 채 `<summary>용어 N개</summary>`, 항목마다 `term_ko (term_en)` — 영역 라벨 — 정의. 다른 영역의 뜻이 있으면 개수를 덧붙인다. 항목이 0이면 그리지 않는다. 2단계에서 항목이 `/glossary#<headword_key>` 로 링크한다. `PostDetail` 의 태그 아래, "제작 기록" 블록 위 |
| `/glossary` 페이지 | 2단계. `src/app/glossary/page.tsx` + BFF `terms` 라우트. 표제어 아래 뜻 번호, 뜻마다 글 목록(`/post/view/{main_category}/{post_id}`). 검색창은 `q`. 범위는 Q-28 |
| i18n | `post.glossary.title`, `post.glossary.count`, `post.provenance.*` 를 `messages/{ko,en}/post.json` 에. 정의 자체는 한국어만이다(Q-27) |
| 실패 정책 | 사전·기록 조회가 실패하거나 느리면 글만 그린다. 주석 달기에서 예외가 나면 원본 JSON 으로 그린다(try/catch, 경고 로그) |

대안으로 ProseMirror 장식(`Decoration.inline`) 플러그인이 있다. 문서를 바꾸지 않고 마크를 자동으로 보존하지만, 장식은 DOM 문자열이라 React 툴팁을 직접 못 쓰고 이벤트 위임과 패널 분리가 필요하다. `TermTooltip` 을 그대로 쓰라는 요구와 탭 토글을 React 안에서 처리하는 쪽이 단순해 인라인 노드를 골랐다(D-66).

### 2.9 ES 자리

지금은 `es.vansdevblog.online` 의 `posts` 색인만 쓴다. 용어 검색(자동완성, 오타 허용, 정의 본문 검색)이 필요해지면 `glossary_senses` 에서 파생한 `glossary` 색인을 `services/indexing.py` 와 같은 방식으로 붙인다. 문서 = 뜻 하나(`sense_key`, `term_ko`, `term_en`, `aliases`, `domain`, `definition`, `post_ids`), `korean_analyzer`·`english_analyzer`·`suggest`(`models/post.py` 의 매핑 패턴). 쓰기는 승인·병합 때 동기화하고, Mongo 가 원천이라 전체 재색인은 `agents glossary reindex` 하나다. 그때까지 읽기 API 는 Mongo 정규식으로 충분하다(표제어 몇백 개).

## 3. 제작 기록 (provenance)

### 3.1 목적과 원칙

- 글마다 문서 하나. 영수증(`agent_calls`)에서 만들되, 필요한 값을 복사해 **자족적**이다. 영수증이 정리돼도 남는다.
- LLM 호출 없음. 순수 함수 `build_provenance(draft, round, calls) -> PostProvenance`(`services/provenance.py`)다. 입력이 같으면 결과가 같아 다시 만들 수 있다(`--rebuild`).
- `publishing.py` 의 머리말("이 글은 **{작성 모델}** 모델이 작성하고 … 교차 리뷰를 거쳐")은 그대로 둔다. 머리말은 본문에 영향을 준 모델만 적고(`_model_line`), 이 문서는 전체 읽기·수준 평가·용어 추출·실패한 시도까지 전부 보인다.

### 3.2 스키마

```python
class ProvenanceStageKind(StrEnum):
    TOPIC = "TOPIC"                 # 주제 제안
    WRITE = "WRITE"                 # 작성 (v1)
    REVIEW = "REVIEW"               # 렌즈별 리뷰 (FULL·SPOT_CHECK)
    FACT_CHECK = "FACT_CHECK"       # 사실 확인 (검색 + 변환)
    ARBITRATION = "ARBITRATION"     # 중재 (검색 포함)
    REVISION = "REVISION"           # 수정 (v2…)
    POLISH = "POLISH"               # 다듬기
    FINAL_READ = "FINAL_READ"       # 최종 읽기
    ASSESS_LEVEL = "ASSESS_LEVEL"   # 수준 판정
    GLOSSARY = "GLOSSARY"           # 용어 추출


class ProvenanceModel(BaseModel):
    provider: str
    model_id: str
    effort: str | None


class ProvenanceCall(BaseModel):
    """영수증 한 건의 요약. 메시지·출력은 복사하지 않는다."""

    call_id: str
    kind: CallKind
    provider: str
    model_id: str
    effort: str | None
    prompt_template_version: str
    attempt_number: int
    succeeded: bool                     # output_parsed_ok 이고 error 가 없다
    usage: TokenUsage
    latency_ms: int
    started_at: datetime


class ProvenanceStage(BaseModel):
    stage: ProvenanceStageKind
    label: str                          # 한국어 표시 이름. 프런트가 그대로 보인다
    lens: ReviewLens | None = None      # REVIEW·FACT_CHECK
    scope: ReviewScope | None = None    # REVIEW·FACT_CHECK (draft.reviews 에서 call_id 로 찾는다)
    target_version: int | None = None
    item_id: str | None = None          # ARBITRATION
    models: list[ProvenanceModel]       # 이 단계에 쓰인 (벤더, 모델, effort) 집합
    prompt_template_versions: list[str]
    calls: list[ProvenanceCall]         # 실패한 시도 포함. 시작 순
    usage: TokenUsage                   # calls 합계
    outcome: str                        # 한 줄 결과 (3.3절 표)


class ProvenanceVersion(BaseModel):
    version_number: int
    origin_cause: RevisionCause | None  # None=초안 | REVIEW | HUMAN | POLISH. 게이트 재작성은 직전 값을 잇는다
    model_id: str | None
    passed_validation: bool
    call_id: str


class PostProvenance(BaseModel):
    """post_provenance 문서."""

    post_id: str = Field(alias="_id")   # Post BE 게시글 id
    draft_id: str
    round_id: str
    round_seq: int
    author_slot: AgentSlot
    author_provider: str                # 발행 계정의 벤더 (D-45). 블로그 작성자 이름의 근거
    writer: ProvenanceModel             # v1 을 쓴 모델
    title: str
    published_at: datetime
    final_version: int                  # 발행된 본문 버전
    authored_version: int               # 다듬기 전 발행본 (머리말의 "v1 → vN")
    versions: list[ProvenanceVersion]
    iteration: int
    level: PostLevel | None             # 평가자 라벨. 사람 경로 글은 None
    user_level: PostLevel | None
    prompt_template_versions: list[str] # 전체 집합 (v3·v4 가 섞인 글은 둘 다)
    stages: list[ProvenanceStage]       # 3.3절 순서
    usage_total: TokenUsage
    call_count: int
    failed_attempts: int                # error 가 있거나 파싱 실패한 시도 수
    schema_version: int = 1
    built_at: datetime
    built_by: Literal["publish", "reconcile", "cli"]
```

| 컬렉션 | 문서 | 인덱스 |
|---|---|---|
| `post_provenance` | `PostProvenance` | `_id`(게시글 id). `draft_id` unique. `round_id` |

### 3.3 영수증 → 단계 매핑

영수증을 `(kind, lens, target_version, item_id)` 로 묶으면 한 묶음이 한 단계의 시도들이다. 단계의 결과(`outcome`)는 영수증이 아니라 글 문서에서 `call_id` 로 찾는다(`versions[].call_id`, `reviews[].call_id`, `arbitrations[].call_id`, `final_read.call_id`, `level_assessment.call_id`, `glossary.call_id`).

| 영수증 | 단계 | `outcome` |
|---|---|---|
| `PROPOSE_TOPIC`, `draft_id` 없음, `slot == author_slot`(라운드 영수증에서 가져온다) | `TOPIC` | "시도 N, 확정 시도 M, 제목" |
| `WRITE_DRAFT` | `WRITE` | "v1, 게이트 통과/실패" |
| `REVISE_DRAFT`(`target_version` 별) | `REVISION` | "vN, 원인(REVIEW·HUMAN·GATE 재작성), 수용 a·기각 b" |
| `REVIEW`(`lens`·`target_version` 별) | `REVIEW` | "FULL/SPOT_CHECK, 판정, CRITICAL c·MAJOR m·MINOR n" |
| `FACT_CHECK`(`item_id` 없음) + 같은 `lens`·`target_version` 의 `FACT_TRANSFORM` | `FACT_CHECK` | "주장 k건: SUPPORTED s·REFUTED r·UNVERIFIABLE u" |
| `ARBITRATE` + 같은 `item_id` 의 `FACT_CHECK`(중재 검색) | `ARBITRATION` | "UPHOLD_REVIEWER/UPHOLD_AUTHOR" |
| `POLISH_TONE`, `POLISH_FORMAT`(옛 글) | `POLISH` | "적용 a·버림 d, 새 버전 vN" 또는 "건너뜀" |
| `FINAL_READ` | `FINAL_READ` | "지적 f건(차단 b)" |
| `ASSESS_LEVEL` | `ASSESS_LEVEL` | "수준 L" |
| `GLOSSARY`(이 문서 2절) | `GLOSSARY` | "용어 t건(보류 p)" 또는 "건너뜀" |

옛 글(슬롯 리뷰어, 라우팅 전)은 `lens` 가 비어 있을 수 있다. 그때는 `reviews[]` 의 `call_id` 로 렌즈를 찾고, 그래도 없으면 `slot` 만 적는다. 단계 순서는 위 표 순서이되 같은 종류는 `target_version`·시작 시각 순이다.

### 3.4 생성 시점과 쓰기

- 발행 성공의 따라 쓰기(2.2절 "발행 연결")에서 `$setOnInsert`. `CONFIRM_PUBLISHED` 도 같다.
- `RoundService.reconcile` 이 `PUBLISHED` 이고 `publication.derived_at` 이 없는 글의 것을 마저 만든다. 이 기능을 배포하면 **이미 발행된 글 전부**의 기록이 첫 스캔에서 자동으로 생긴다(LLM 호출이 없으니 싸다). 영수증이 이미 지워진 글은 글 문서만으로 만들고 `calls` 가 빈 단계로 남는다.
- 보충·재생성: `agents provenance build <draft_id> [--rebuild]`, `agents provenance build --missing`. `--rebuild` 는 스키마가 바뀌었을 때 `replace_one`.
- `agent_calls` 정리 정책은 아직 없다. 생기면 이 문서가 먼저 만들어진 글의 영수증만 지운다(Q-24).

### 3.5 API

| 메서드 | 경로 | 동작 |
|---|---|---|
| `GET` | `/api/v1/agents/posts/{post_id}/provenance/` | `ProvenancePublic`. 키 없이 공개. 없는 글(사람 글, 아직 안 만들어짐)은 404 |

`/api/v1/agents/` 라우터는 라우터 수준 `verify_agents_key` 다. 이 엔드포인트는 공개라 같은 접두의 **별도 라우터** `routers/post_provenance.py`(`prefix="/api/v1/agents/posts"`, 의존성 없음)에 둔다. 공개 범위는 Q-24 다. `ProvenancePublic` 은 `PostProvenance` 에서 `built_by` 를 빼고 `calls[].error` 를 넣지 않은 것이다(영수증 오류 문자열은 벤더 응답을 담을 수 있다). 변환은 `services/provenance.py`, 골든은 `tests/golden/provenance.json`.

### 3.6 프런트

`src/components/features/post/view/PostProvenance.tsx`(클라이언트, i18n). `<details>` 접힌 채 `<summary>제작 기록</summary>`. 첫 줄: 작성 모델, 버전 `v1 → vN`, 총 토큰. 그 아래 표: 단계 | 모델(벤더·effort) | 프롬프트 버전 | 시도(실패 수) | 토큰 | 결과. 단계 이름은 `label` 그대로. `PostDetail` 의 "용어" 블록 아래. 데이터가 없으면(404) 그리지 않는다. 색은 쓰지 않는다(개발 도구 UI 기준).

## 4. 코드 배치 (06 문서 2절에 더할 것)

```
app/
├─ routers/
│  ├─ glossary.py              공개 읽기: /api/v1/glossary/posts/{id}/, /headwords/{key}/, /terms/
│  └─ post_provenance.py       공개 읽기: /api/v1/agents/posts/{id}/provenance/ (키 없음, 별도 라우터)
├─ schemas/
│  ├─ glossary.py              PostGlossaryPublic, PostGlossaryItemPublic, OtherSensePublic, DomainPublic, GlossaryTermList, HeadwordPublic, SensePublic, PostRef
│  └─ provenance.py            ProvenancePublic
├─ services/
│  ├─ glossary.py              GlossaryService: extract(추출 단계), resolve_candidate(등록 확인·뜻 선택), 승인·별칭·resolve·drop·merge·check, terminology_for
│  ├─ glossary_views.py        읽기 전용 조회와 엔티티 → 응답 변환
│  ├─ glossary_matching.py     normalize_key, similar_headwords(2-gram Jaccard·편집 거리). 순수 함수
│  └─ provenance.py            build_provenance(순수 함수), ProvenanceService(쓰기·보충), build_provenance_public
├─ agents/
│  ├─ contracts.py             GlossaryInput·GlossaryOutput·KnownHeadword·…, TerminologyEntry
│  ├─ builders.py              build_glossary_input, render_messages 의 _TEMPLATE_BY_INPUT 에 추가
│  ├─ parsers.py               glossary_output_check(스키마 외 위반은 버리기)
│  └─ prompts/v4/haiku/glossary.md
├─ repositories/
│  ├─ glossary.py              glossary_headwords·glossary_senses·post_glossary·glossary_counters(next_sense_key). ensure_indexes(unique 다중키)
│  ├─ post_provenance.py       post_provenance
│  └─ post_ref.py              posts·categories 읽기 전용 (PostRef 용, 비동기)
├─ models/
│  ├─ glossary.py              GlossaryHeadword, GlossarySense, PostGlossary, PostGlossaryItem, PendingTerm, GlossaryRecord, DOMAINS(영역 슬러그 → 표시 이름)
│  ├─ provenance.py            PostProvenance 와 하위 모델
│  ├─ enums.py                 CallKind.GLOSSARY·GLOSSARY_DEDUPE, PostReviewStage.GLOSSARY, GlossaryStatus, ProvenanceStageKind
│  ├─ post_draft.py            PostDraft.glossary: GlossaryRecord | None, Publication.derived_at, finish_glossary(record)
│  ├─ publishing_round.py      terminology: list[TerminologyEntry] (2.7절)
│  └─ routing.py               ROUTE_GLOSSARY, validate_snapshot 에 select_any 추가
├─ core/config.py              glossary_full_list_max=300, glossary_min_terms=8, glossary_max_terms=12, glossary_surface_max_chars=40, glossary_terminology_max=150
└─ dependencies.py             ensure_agent_indexes 에 두 리포지토리, build_glossary_service, get_glossary_view_service, get_provenance_service
cli/__main__.py                agents glossary …, agents provenance …
```

의존 방향은 CONVENTIONS 와 같다. `GlossaryService` 는 `AgentCallService` 와 리포지토리를 받고, `DraftPipelineService` 가 `PolishingService` 처럼 조립한다. `PublishingService`·`DraftDecisionService`·`RoundService.reconcile` 은 `DerivedRecordsWriter`(글 하나의 `post_glossary`·`post_provenance`·`post_ids`·`derived_at` 따라 쓰기) 하나를 공유한다.

## 5. 도입 순서

Post BE 는 바꾸지 않는다. FastAPI 를 먼저 끝내고 vans_story 를 잇는다. 단계마다 독립적으로 머지할 수 있다.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| G-1 | `models/glossary.py`·`provenance.py`, 열거형, `glossary_matching.py`, 리포지토리와 인덱스, `build_provenance` 순수 함수 | DB 없는 단위 테스트. `FakeAsyncCollection` 으로 unique 다중키 인덱스 동작(같은 키 두 표제어 거절). 완료(2026-11-07) |
| G-2 | `GLOSSARY` 계약·템플릿·파서·빌더, 라우트(`ROUTE_GLOSSARY`, `routes.example.json`), `PostReviewStage.GLOSSARY`, `GlossaryService.extract`·`resolve_candidate`, `DraftPipelineService` 단계 | `PipelineHarness` 시나리오: ⑭ 추출 성공 → 제안 저장 → `READY_TO_PUBLISH` ⑮ 호출 실패해도 `READY_TO_PUBLISH`, `skipped` 기록 ⑯ 라우트 없는 옛 스냅샷은 건너뜀 ⑰ 재개 때 끝낸 추출을 되풀이하지 않음. 완료(2026-11-07) |
| G-3 | 발행 따라 쓰기(`DerivedRecordsWriter`), `Publication.derived_at`, `reconcile` 확장, `CONFIRM_PUBLISHED` 경로 | 발행 성공 뒤 `post_glossary`·`post_provenance`·`post_ids` 존재. 발행 반영 뒤 끊고 다시 스캔하면 정리가 마저 씀. 되풀이해도 문서 하나. 완료(2026-11-07) |
| G-4 | CLI `agents glossary …`(2.5절), `agents provenance …` | `pending → approve-sense → show` 흐름 테스트. 구분 문구 불변식. `extract --missing` 이 사람 경로 글을 채움. 완료(2026-11-07) |
| G-5 | 읽기 API 3개 + 제작 기록 API, 골든 4개 | `dependency_overrides` 없이 `FakeAsyncDatabase` 로 실제 서비스가 돌고 골든과 값까지 같음. 미승인 항목이 응답에 없음. 사람 글은 404. 완료(2026-11-07) |
| G-6 | vans_story: BFF 라우트 4개, `serverActions`, `annotateGlossary`, `GlossaryTerm` 노드, `useGlossaryTerm` 훅, `TermTooltip` 의 `tapToggle`·`content`, `PostGlossary`·`PostProvenance` 블록, i18n | 로컬에서 파이프라인 글은 첫 등장 툴팁(호버·포커스·탭. 이 글의 뜻과 영역, 다른 영역의 뜻)과 두 블록이 보이고, 사람 글은 아무것도 달라지지 않음. 조회 실패 시 글만 그려짐. 기존 `TermTooltip` 사용처 변화 없음 |
| G-7 | 운영: 라운드 1회 뒤 `agents glossary pending` 검토·승인, `extract --missing` 으로 기존 글 채우기, 첫 스캔의 제작 기록 자동 생성 확인 | 사용자가 블로그에서 확인. 승인율·보류율을 `agents metrics` 에 더한다 |
| G-8 | 표기 표준 입력(2.7절, 다음 프롬프트 버전) | 발행본의 `avoid` 표기 횟수가 줄어듦 |
| G-9 | 2단계: `/glossary` 페이지, `GLOSSARY_MATCH` 2단계 호출, ES 색인, 관리 API | 각각 Q-28·Q-20·Q-26·Q-10 결정 뒤 |

## 6. 테스트 전략 (06 문서 6절에 더할 행)

| 범위 | 방법 |
|---|---|
| `services/glossary_matching.py` | `normalize_key` 표(2.1절), 2-gram Jaccard 후보 순서, 편집 거리 동률 |
| `services/glossary.py` | 등록 확인 ①~④ 각 1건, 코드 정확 일치가 AI `same_as` 를 이김, `same_as` 목록 밖 → `None`, 새 표제어 `DuplicateKeyError` → ①, 뜻 선택 표 행마다 1건, 뜻 키 순번 발급(동시 발급에 중복 없음), `domains` 슬러그 검증(모르는 것은 빼고 비면 `other`), `surface` 검증(펜스 안·인라인 코드·줄바꿈·길이), 같은 글 안 중복 제거, 제안 저장 멱등, 승인 때 구분 문구 불변식, 병합 ①~④와 중단 뒤 `check`, `terminology_for` 상한·우선순위 |
| `services/glossary.py`(후보 검사) | 스키마 위반만 재시도. 앵커 없음·`surface` 없음·전제 지식·중복은 `discarded` 에 사유 |
| `services/draft_pipeline.py` | 5절 G-2 의 ⑭~⑰. `ScriptedLLMClient` 의 `_KIND_MARKERS` 에 `glossary.md` 의 고유 문구를 더한다 |
| `services/publishing.py`·`round.py` | G-3 기준. 라운드 중단과 겹친 발행(`external_post_id` 만 의도에 남는 경우)에는 파생 문서를 만들지 않음 |
| `services/provenance.py` | 순수 함수: 3.3절 표 행마다 1건, 실패 시도 포함 합계, 옛 글(슬롯 리뷰어·렌즈 없음), 영수증 없는 글, `authored_version`·`final_version` 구분 |
| `routers/` | 골든 `glossary_post.json`·`glossary_terms.json`·`glossary_headword.json`·`provenance.json`. 미승인 제외(`other_senses` 도 승인된 뜻만), 404 |
| `cli/` | 명령마다 서비스 호출과 종료 코드. `extract` 의 덮어쓰기 경고 |
| vans_story | `annotateGlossary` vitest(첫 등장, 코드 제외, 마크 보존, 못 찾음, 예외 시 원본), `useGlossaryTerm`(컨텍스트 우선, 없으면 조회·키별 캐시), `TermTooltip` 탭 토글·바깥 클릭 닫힘·`content` 렌더(기존 호버 테스트 유지) |

## 7. 지표 (07 문서 4절에 더할 행)

| 지표 | 의미 |
|---|---|
| 글당 추출 용어 수와 `discarded` 사유별 건수 | 프롬프트의 선정 기준과 `surface` 규칙이 지켜지는지 |
| `resolution` 비율(EXACT·SAME_AS·NEW·HUMAN) | 사전이 쌓일수록 NEW 가 줄어야 한다 |
| 제안 대비 승인율, 보류 비율 | 지어낸 정의·틀린 등록 판정의 빈도 |
| 발행본의 `avoid` 표기 횟수 | 표기 표준 경로(2.7절)의 효과 |
| 추출 호출 토큰·실패율 | haiku 로 충분한지 |

## 8. 결정 (07 문서 1절에 옮길 행)

| ID | 결정 | 이유 | 대안 |
|---|---|---|---|
| D-63 | 용어 사전은 공용 Mongo(D-43)의 `glossary_headwords`·`glossary_senses`·`post_glossary` 세 컬렉션이고 단위는 뜻이다. 표제어 키는 `normalize_key(term_en or term_ko)`, 뜻 키는 단어도 영역도 담지 않는 순번(`s00042`)이고 영역은 뜻의 속성 `domains`(고정 표 `DOMAINS`, 여럿 가능)다. 별칭은 `lookup_keys` unique 다중키 인덱스로 표제어 하나에만 속한다. Post BE 는 바꾸지 않고 FastAPI 가 읽기 API 를 키 없이 공개한다 | 같은 단어가 의미마다 다른 뜻("토큰")이라 단어 단위면 툴팁이 틀리고, 영역이 다르다고 뜻이 다른 것도 아니다("트랜잭션"은 DB 글에서나 백엔드 글에서나 같다. 2026-11-07 사용자 지적). 키에 단어나 영역이 들어가면 병합·영역 확장 뒤 어긋난다. 표제어 키를 코드가 만들어야 등록 여부를 AI 없이 먼저 가릴 수 있다. 발행 글의 부속이라 비밀이 없다 | MariaDB·ES 저장, 단어 단위 사전, `{headword}.{domain}` 뜻 키(영역이 의미를 갈라 트랜잭션이 둘이 된다), `{headword}.{n}` 뜻 키(병합 뒤 접두가 남는다), Post BE DTO 확장, BFF 가 내부 키를 붙여 호출 |
| D-64 | 추출은 `POST_REVIEW` 의 넷째 단계 `GLOSSARY`(저가 모델, `select_optional`)이고 실패해도 건너뛴다(다듬기 정책). 결과는 `PostDraft.glossary` 에 두고 발행 성공의 따라 쓰기가 `post_glossary` 로 옮긴다. 사람 경로 글은 보충 명령이 채운다. `GLOSSARY` 는 선택 라우트라 없어도 라운드를 만들고, 라우트가 없는 라운드는 호출 없이 건너뛴다 | 발행본이 확정된 뒤여야 표면형이 맞고, 발행 전이어야 기존 `_step`·포인터 저장으로 끝난다. 게시글 id 는 발행 뒤에야 있다. 용어가 없다고 글을 멈출 이유가 없다 | 발행 뒤 별도 스캔으로 추출(러너 대상이 늘고 `is_active` 규칙이 깨진다), 리뷰 단계에 포함(본문이 아직 바뀐다) |
| D-65 | 등록 확인은 코드 정확 일치 → AI 의 `same_as`(목록 안만) → 코드 n-gram 후보 → 새 표제어 순이고, 코드 판정이 AI 판정보다 우선한다. 새 표제어·별칭·뜻은 `PROPOSED` 로 저장하고 사람 승인(`APPROVED`) 뒤에만 표시한다. 승인은 전역이다. 사전이 `GLOSSARY_FULL_LIST_MAX` 를 넘으면 2단계 호출로 나눈다 | 지어낸 정의와 잘못된 동일시가 독자에게 보이면 사전이 신뢰를 잃는다. 정확 일치는 AI 가 틀릴 수 없는 영역이다. 전체 목록을 보내는 동안은 AI 가 이미 봤으니 2차 호출이 필요 없다 | 자동 표시 뒤 사후 수정, 매번 AI 판정, 임베딩 유사도 |
| D-66 | 첫 등장 표면형만 감싸고, 툴팁은 이 글에서 쓰인 뜻을 영역 라벨과 함께 맨 위에, 같은 표제어의 다른 승인된 뜻을 그 아래 보인다. 툴팁 컴포넌트는 뜻 키 기반이다: 글 페이지는 글 단위 응답으로 내용을 미리 받고, 없을 때만 표제어 키로 조회한다. vans_story 는 보일 때 TipTap JSON 에 인라인 노드 `glossaryTerm` 을 넣어 React 노드 뷰가 `TermTooltip`(탭 토글·`content` 추가)을 그린다. 주석 달린 JSON 은 저장하지 않는다. 글 끝 "용어" 블록은 전부를 접은 채 보인다 | 뜻 하나만 보일 거면 각주로 족하다. 툴팁의 값은 이 글의 뜻이 어느 영역의 것이고 다른 곳에서는 어떻게 쓰이는지를 함께 보이는 데 있다(2026-11-07 사용자 결정). 글 페이지에서 호버 때 조회하면 첫 호버가 늦고 모바일 탭에서 더 티가 나므로 미리 받고, 키 기반 훅이라 다른 페이지에서도 같은 컴포넌트를 쓴다. `TermTooltip` 재사용과 탭 토글을 React 안에서 끝내려면 노드 뷰가 단순하다. 저장하면 에디터 스키마가 오염된다 | 글이 가리킨 뜻 하나만 표시, 호버 때마다 키로 조회(첫 호버 지연), ProseMirror 장식 플러그인 + 이벤트 위임(마크 보존은 쉽지만 React 툴팁을 못 쓴다), 서버에서 HTML 생성 |
| D-67 | 제작 기록은 발행 때 영수증에서 만드는 자족적 글 단위 문서 `post_provenance` 다. 순수 함수로 만들고 정리가 빠진 글을 마저 만들며, 보충·재생성 명령이 있다. 머리말 노트는 그대로 두고 이 블록이 전체(실패한 시도, 전체 읽기·수준 평가·용어 추출 포함)를 보인다 | 영수증은 정리될 수 있고, 글마다 수십 건을 모으는 조회를 매번 하지 않는다. 결정론적이라 다시 만들 수 있다. 머리말은 본문에 영향을 준 모델만 적는 요약이라 역할이 다르다 | 영수증을 매번 조회해 응답, 머리말을 늘려 본문에 넣기(본문이 길어지고 수정이 불가능하다) |
| D-68 | 작성자 표기 표준은 승인된 표제어에서 파생한 `terminology` 입력(라운드 스냅샷)을 주제 제안·작성·수정·다듬기 프롬프트에 넣는 것이다. 템플릿 문구는 새 프롬프트 버전에 넣는다 | 음차는 주제 제안에서 생겨 끝까지 퍼지므로 첫 호출부터 막아야 한다. 배포된 버전 디렉터리는 고치지 않는다 | 다듬기 정규식 치환만(라틴 토큰 보존 규칙에 걸린다), 리뷰 지적(표현은 MINOR 라 막지 못한다) |
| D-69 | `glossary.md` 템플릿은 `prompts/v4/haiku/glossary.md` 와 `prompts/v3/glossary.md`(평면)에 같은 파일로 둔다 | 로더는 활성 버전 디렉터리만 보므로 기본 `prompt_version` 이 v3 인 동안에도 찾아야 한다. 배포된 버전 디렉터리에 새 이름을 더하는 것은 기존 호출의 문구를 바꾸지 않는 추가다(Q-25 해소) | v4 전환 뒤 도입, GLOSSARY 만 고정 버전 |

## 9. 문서 반영 목록 (구현 때)

2026-11-07 구현과 함께 아래를 02~07·README·`routes.example.json`·`.env.example`·CLAUDE.md 에 반영했다(CLAUDE.md 규칙). 05 의 가드레일 표는 04 7절에 있어 거기에 넣었다.

| 문서 | 위치 | 변경 |
|---|---|---|
| 02 | 1절 열거형 | `CallKind.GLOSSARY`·`GLOSSARY_DEDUPE`, `PostReviewStage.GLOSSARY`(`ASSESS_LEVEL` 과 `DONE` 사이), `GlossaryStatus`, `ProvenanceStageKind` |
| 02 | 2절 엔티티 관계 | `PostDraft.glossary?: GlossaryRecord`, `post_review_stage` 주석을 `POLISH → FINAL_READ → ASSESS_LEVEL → GLOSSARY → DONE` 으로, `Publication.derived_at`, `PublishingRound.terminology`, 새 집합 루트 `GlossaryHeadword`·`GlossarySense`·`PostGlossary`·`PostProvenance` |
| 02 | 5절 메서드 표 | `finish_glossary(record)`: `POST_REVIEW(GLOSSARY)` → `READY_TO_PUBLISH`. `record_level_assessment` 의 결과 상태를 `변화 없음(GLOSSARY)` 으로 |
| 02 | 6절 저장 표 | 컬렉션 4개와 인덱스(2.1절·3.2절 표). `lookup_keys` unique 다중키 설명 |
| 03 | 전이표 | 22e 를 `POST_REVIEW(GLOSSARY)` 로, 새 22f `POST_REVIEW(GLOSSARY)` → `READY_TO_PUBLISH`(성공·실패 모두), 5·5a 행의 "(다듬기 제외)"를 "(다듬기·용어 추출 제외)"로, 다이어그램 |
| 04 | 1절 표 | "발행 성공" 행에 `post_glossary`·`post_ids`·`post_provenance`·`derived_at` 따라 쓰기와 정리 조건 |
| 04 | 5절 발행 전 단계 표 | "용어 추출" 행(2.2절 표) |
| 04 | 8절 발행 | 8단계 뒤 따라 쓰기 순서, `reconcile` 설명 |
| 04 | 9절 실패 처리 | "용어 추출 실패 → 건너뜀" 행 |
| 05 | 2.7 표 | `GLOSSARY`(`GlossaryInput` → `GlossaryOutput`), `GLOSSARY_DEDUPE` 행. 입력 필드 `terminology` 를 네 호출에 |
| 05 | 7절 가드레일 | 2.3절 프롬프트 수준 규칙 표 |
| 06 | 2절 구조도, 3절 라우터·CLI 표, 4절 `Settings`, 6절 테스트 | 4절·2.5절·2.6절·3.5절·6절 내용 |
| 07 | 1절 결정 | D-63~D-68(8절) |
| 07 | 2절 미결 | Q-20~Q-28(10절) |
| 07 | 3절 로드맵 | 9단계: 용어 사전·제작 기록(G-1~G-9) |
| 07 | 4절 지표 | 7절 표 |
| README | 표·상태 | 08 행(이번에 추가), 구현 때 상태 줄 |
| `routes.example.json` | – | `GLOSSARY` 라우트(haiku, effort low, 8000) |
| `.env.example` | – | `GLOSSARY_FULL_LIST_MAX`, `GLOSSARY_MIN_TERMS`, `GLOSSARY_MAX_TERMS`, `GLOSSARY_SURFACE_MAX_CHARS`, `GLOSSARY_TERMINOLOGY_MAX` |
| CLAUDE.md | 에이전트 파이프라인 | 진행 줄과 운영 절차(`agents glossary pending` → 승인) |
| vans_story `api.md`·`CONVENTIONS.md` | BFF 목록 | 라우트 3개, `GlossaryTerm` 노드가 뷰어 전용이라는 규칙 |

## 10. 결정 대기 항목

구현 전에 사용자가 정할 것. 기본 가정으로 진행할 수 있다.

| ID | 질문 | 영향 | 기본 가정 |
|---|---|---|---|
| Q-20 | 전체 목록 상한 `GLOSSARY_FULL_LIST_MAX` 수치와 2단계 호출(`GLOSSARY_MATCH`) 도입 시점 | 입력 토큰(표제어 300개 × 뜻 1~2개면 수천 토큰, haiku 라 작다) | 300. 넘기 전까지 2차 호출을 만들지 않는다 |
| Q-21 | AI 의 `same_as` 판정(`SAME_AS`)으로 **이미 승인된 뜻**에 붙은 항목을 글 단위 확인 없이 바로 보일지 | 첫 글부터 툴팁이 보이는 속도 vs 틀린 동일시 노출. 항목 단위 확인을 두려면 `PostGlossaryItem` 에 확인 플래그가 필요하다 | 보인다(전제대로: 승인이 필요한 것은 새 표제어·별칭·뜻이다). 표시 조건은 뜻·표제어의 `APPROVED` 뿐이고, 틀린 동일시는 `drop` 으로 지운다 |
| Q-22 | 사람이 쓴 글의 용어 추출(TipTap JSON → 텍스트 변환 뒤 같은 호출) | 변환기 하나, `post_glossary.draft_id` 가 비는 문서 | 범위 밖. 파이프라인 글이 쌓인 뒤 |
| Q-23 | 다듬기 정규식 단계에 승인 별칭 → `term_ko` 치환을 넣을지 | 한국어 ↔ 한국어 치환만 가능. "스테이트리스" → "무상태" 는 되고 "무상태(stateless)" 는 안 된다 | 넣지 않는다. 표기 표준은 작성 프롬프트 입력(D-68)으로 |
| Q-24 | 제작 기록 공개 범위(토큰 수·effort·실패 시도·프롬프트 버전까지 공개할지)와 `agent_calls` 정리 정책 | 독자에게 보이는 정보, 저장 비용 | 전부 공개(오류 문자열만 제외). 영수증 정리는 제작 기록이 있는 글만, 시점은 미정 |
| Q-25 | v3 운영 중에 도입할 때 템플릿 위치 | 해소(2026-11-07, D-69): `v3/glossary.md` 와 `v4/haiku/glossary.md` 에 같은 파일 | – |
| Q-26 | ES 용어 색인 시점 | `services/indexing.py` 확장, 재색인 명령 | 전역 페이지에서 Mongo 정규식이 느려질 때 |
| Q-27 | 영어 UI 에서 정의를 영어로도 보일지 | 뜻마다 `definition_en` 과 번역 호출 | 한국어만. 블록 제목만 i18n |
| Q-28 | `/glossary` 전역 페이지 범위(검색, 영역 필터, 글 목록) | 프런트 2단계 범위 | 표제어 목록 + 뜻 번호 + 글 링크. 검색은 `q` 하나 |
| Q-29 | 글 끝 "용어"·"제작 기록" 블록을 기본 접힘으로 둘지, 요약 노트("이 글은 어떻게 만들어졌나") 바로 아래 둘지 태그 아래 둘지 | 독자 동선 | 둘 다 접힘. 태그 아래(요약 노트는 본문 안에 있다) |
| Q-30 | 영역 표 `DOMAINS` 를 코드 고정으로 둘지 Mongo 설정 문서(`agent_routes` 의 `LEVEL_CRITERIA` 처럼)로 둘지, 초기 목록 | 표시 이름의 i18n, 늘릴 때 배포가 필요한지 | 코드 고정 12개(2.1절). 늘리는 일이 잦으면 문서로 옮긴다 |
| Q-31 | 병합 제안(`dedupe`)에 LLM 판정을 붙일지 | 템플릿·계약 하나 더. 지금은 코드 유사도 쌍만 찍는다(2.4절) | 사전이 몇백 개가 된 뒤 |
