"""용어 사전·제작 기록 공개 API 테스트. 키 없이 읽히고, 응답 계약은 golden(glossary_*.json·provenance.json)이다.

FakeAsyncDatabase 를 get_async_database 로 꽂아 실제 리포지토리·서비스가 돈다. 시각은 고정해 golden 과 그대로 비교한다.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.core.config import Settings, get_settings
from app.dependencies import get_async_database
from app.main import app
from app.models.enums import AgentSlot, CallKind, GlossaryStatus, PostStatus
from app.models.glossary import GlossaryHeadword, GlossarySense, PendingTerm, PostGlossary, PostGlossaryItem
from app.models.post_draft import Publication
from app.repositories.glossary import GlossaryRepository
from app.repositories.post_provenance import PostProvenanceRepository
from app.services.provenance import build_provenance
from tests.conftest import load_golden
from tests.factories import finish_post_review, make_call, make_draft, make_round, write_next_version
from tests.fakes import FakeAsyncDatabase

T = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
POST_1 = "6734a1b2c3d4e5f6a7b8c9d0"
POST_2 = "6734a1b2c3d4e5f6a7b8c9d1"
CATEGORY = "6734a1b2c3d4e5f6a7b8c9c0"


def _headword(key: str, term_ko: str, term_en: str | None, status: GlossaryStatus, aliases=()) -> GlossaryHeadword:
    lookup = [key, term_ko.replace(" ", ""), *aliases]
    return GlossaryHeadword(
        headword_key=key,
        term_ko=term_ko,
        term_en=term_en,
        aliases=list(aliases),
        lookup_keys=list(dict.fromkeys(lookup)),
        status=status,
        approved_by="사람" if status == GlossaryStatus.APPROVED else None,
        approved_at=T if status == GlossaryStatus.APPROVED else None,
        created_at=T,
        updated_at=T,
    )


def _sense(key, headword_key, domains, definition, distinguisher="", status=GlossaryStatus.APPROVED, post_ids=()):
    return GlossarySense(
        sense_key=key,
        headword_key=headword_key,
        domains=list(domains),
        definition=definition,
        distinguisher=distinguisher,
        status=status,
        post_ids=list(post_ids),
        created_at=T,
        updated_at=T,
    )


async def _seed(database: FakeAsyncDatabase) -> None:
    glossary = GlossaryRepository(database)
    await glossary.ensure_indexes()
    for headword in (
        _headword("stateless", "무상태", "stateless", GlossaryStatus.APPROVED, aliases=["스테이트리스"]),
        _headword("token", "토큰", "token", GlossaryStatus.APPROVED),
        _headword("nori", "노리", "nori", GlossaryStatus.PROPOSED),
    ):
        assert await glossary.add_headword_if_missing(headword)
    for sense in (
        _sense(
            "s00001",
            "token",
            ["auth"],
            "서버가 서명해 발급하는 인증용 문자열",
            "언어 모델 토큰이 아니라 인증용 서명 문자열",
            post_ids=[POST_1],
        ),
        _sense(
            "s00002",
            "token",
            ["llm"],
            "언어 모델이 글을 자르는 입력 단위",
            "인증 토큰이 아니라 언어 모델의 입력 단위",
            post_ids=[POST_2],
        ),
        _sense(
            "s00003",
            "stateless",
            ["backend", "network"],
            "요청 사이에 서버가 클라이언트 상태를 저장하지 않는 설계",
            post_ids=[POST_1],
        ),
        _sense("s00004", "nori", ["search"], "한국어 형태소 분석기", status=GlossaryStatus.PROPOSED, post_ids=[POST_1]),
    ):
        await glossary.add_sense(sense)
    await glossary.save_post_glossary(
        PostGlossary(
            post_id=POST_1,
            draft_id="round-1:A",
            round_id="round-1",
            content_version=1,
            call_id="call-glossary",
            prompt_template_version="v4",
            items=[
                PostGlossaryItem(sense_key="s00003", surface="스테이트리스는", section_id="intro", resolution="NEW"),
                PostGlossaryItem(sense_key="s00001", surface="토큰은", section_id="intro", resolution="EXACT"),
                PostGlossaryItem(sense_key="s00004", surface="노리를", section_id="body-1", resolution="NEW"),
            ],
            pending=[
                PendingTerm(
                    term_ko="인덱스",
                    term_en="index",
                    surface="인덱스를",
                    section_id="body-1",
                    headword_key=None,
                    reason="뜻 미정",
                )
            ],
            extracted_at=T,
            updated_at=T,
        )
    )
    await database["categories"].insert_one({"_id": ObjectId(CATEGORY), "value": "nestjs", "label": "NestJS"})
    await database["posts"].insert_one(
        {"_id": ObjectId(POST_1), "title": "무상태 서버와 토큰", "mainCategoryId": ObjectId(CATEGORY)}
    )
    await database["posts"].insert_one(
        {"_id": ObjectId(POST_2), "title": "언어 모델 토큰", "mainCategoryId": ObjectId(CATEGORY)}
    )

    draft = make_draft(AgentSlot.A, created_at=T, updated_at=T)
    write_next_version(draft)
    finish_post_review(draft)
    draft.status = PostStatus.PUBLISHED
    draft.publication = Publication(published_at=T, final_version=1, external_post_id=POST_1, peer_review_note="")
    calls = [
        make_call("call-topic", draft_id=None, kind=CallKind.PROPOSE_TOPIC, prompt_tokens=10, completion_tokens=5),
        make_call(
            draft.versions[0].call_id, prompt_tokens=100, completion_tokens=50, target_version=1, started_second=1
        ),
    ]
    await PostProvenanceRepository(database).add_if_missing(
        build_provenance(draft, make_round(), calls, built_by="publish", now=T)
    )


@pytest.fixture
def database() -> FakeAsyncDatabase:
    database = FakeAsyncDatabase()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    app.dependency_overrides[get_async_database] = lambda: database
    asyncio.run(_seed(database))
    return database


def test_post_glossary_matches_golden_and_hides_unapproved_senses(client, database):
    response = client.get(f"/api/v1/glossary/posts/{POST_1}/")

    assert response.status_code == 200
    assert response.json() == load_golden("glossary_post")
    assert [item["sense_key"] for item in response.json()["items"]] == ["s00003", "s00001"]  # nori 는 PROPOSED
    assert client.get("/api/v1/glossary/posts/nope/").status_code == 404


def test_terms_match_golden_and_filter_by_normalized_key(client, database):
    response = client.get("/api/v1/glossary/terms/")

    assert response.status_code == 200
    assert response.json() == load_golden("glossary_terms")
    assert [
        each["key"] for each in client.get("/api/v1/glossary/terms/", params={"q": "State Less"}).json()["items"]
    ] == ["stateless"]
    assert client.get("/api/v1/glossary/terms/", params={"page": 2}).json() == {"items": [], "total": 2}
    assert client.get("/api/v1/glossary/terms/", params={"size": 0}).status_code == 400


def test_headword_matches_golden_and_proposed_is_404(client, database):
    response = client.get("/api/v1/glossary/headwords/token/")

    assert response.status_code == 200
    assert response.json() == load_golden("glossary_headword")
    assert client.get("/api/v1/glossary/headwords/nori/").status_code == 404


def test_provenance_is_public_and_matches_golden(client, database):
    response = client.get(f"/api/v1/agents/posts/{POST_1}/provenance/")

    assert response.status_code == 200
    assert response.json() == load_golden("provenance")
    assert client.get("/api/v1/agents/posts/nope/provenance/").status_code == 404
    assert client.get("/api/v1/agents/rounds/", headers={}).status_code == 403  # 내부 API 는 여전히 키가 필요하다
