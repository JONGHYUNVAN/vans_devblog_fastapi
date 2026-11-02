"""운영 명령. 사용법: python -m cli --help (Django 관리 명령 sync_posts_to_elasticsearch·setup_popular_searches 대체)."""

import asyncio
import json
import logging
from typing import Annotated

import typer

from app.clients.mongo import create_async_mongo_client
from app.core.config import get_settings
from app.dependencies import (
    build_draft_decision_service,
    build_pipeline_runner,
    build_round_service,
    ensure_agent_indexes,
    get_index_setup_service,
    get_sync_service,
    open_clients,
)
from app.models.enums import EscalationDecisionKind, RoundStatus
from app.repositories.agent_call import AgentCallRepository
from app.repositories.post_draft import PostDraftRepository
from app.schemas.agent_pipeline import DraftPublic
from app.schemas.sync import SyncOptions

cli = typer.Typer(help="VansDevBlog 검색 서비스 운영 명령", no_args_is_help=True)
agents = typer.Typer(help="에이전트 파이프라인 (설계 agents_design/06 문서 3절 cli)", no_args_is_help=True)
cli.add_typer(agents, name="agents")


@cli.callback()
def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("elasticsearch").setLevel(logging.WARNING)
    logging.getLogger("elastic_transport").setLevel(logging.WARNING)


@cli.command()
def sync_posts(
    batch_size: Annotated[int, typer.Option(min=1, max=500, help="MongoDB 에서 한 번에 읽을 게시글 수")] = 50,
    incremental: Annotated[bool, typer.Option(help="최근 --days 일 안에 생성·수정된 게시글만")] = False,
    days: Annotated[int, typer.Option(min=1, max=365)] = 7,
    clear_existing: Annotated[bool, typer.Option(help="posts 인덱스 문서를 모두 지운 뒤 동기화")] = False,
    dry_run: Annotated[bool, typer.Option(help="색인·삭제 없이 건수만 센다")] = False,
) -> None:
    """MongoDB 게시글을 ES posts 인덱스로 동기화한다. 실패하면 종료 코드 1."""
    options = SyncOptions(
        batch_size=batch_size, incremental=incremental, days=days, clear_existing=clear_existing, dry_run=dry_run
    )
    settings = get_settings()
    with open_clients(settings) as (es, mongo_client):
        result = get_sync_service(es, mongo_client, settings).sync_posts(options)
    typer.echo(json.dumps(result.model_dump(exclude_none=True), ensure_ascii=False, indent=2))
    if result.status == "failed":
        raise typer.Exit(1)


@cli.command()
def sync_status() -> None:
    """MongoDB·ES 연결과 게시글 수를 확인한다 (Django test_mongodb_connection 대체)."""
    settings = get_settings()
    with open_clients(settings) as (es, mongo_client):
        status = get_sync_service(es, mongo_client, settings).get_sync_status()
    typer.echo(json.dumps(status.model_dump(mode="json"), ensure_ascii=False, indent=2))


@cli.command()
def setup_popular(
    recreate: Annotated[bool, typer.Option(help="기존 인덱스를 지우고 새로 만든다 (검색 횟수가 사라진다)")] = False,
) -> None:
    """인기 검색어 인덱스(vans_popular_searches)를 만든다. 이미 있으면 그대로 둔다."""
    settings = get_settings()
    with open_clients(settings) as (es, _):
        index_setup_service = get_index_setup_service(es)
        if recreate:
            index_setup_service.recreate_popular_index()
            typer.echo("인기 검색어 인덱스를 다시 만들었다.")
        elif index_setup_service.ensure_popular_index():
            typer.echo("인기 검색어 인덱스를 만들었다.")
        else:
            typer.echo("인기 검색어 인덱스가 이미 있다.")


def _run_agents(coroutine_factory):
    """비동기 Mongo 클라이언트를 열고 코루틴을 돌린 뒤 닫는다. agents 명령이 같이 쓴다."""
    settings = get_settings()
    if not settings.mongodb_db_name:
        typer.echo("MongoDB DB 이름(MONGODB_DATABASE 또는 URI 경로)이 없다.", err=True)
        raise typer.Exit(1)

    async def _main():
        async_mongo_client = create_async_mongo_client(settings)
        try:
            return await coroutine_factory(async_mongo_client[settings.mongodb_db_name], settings)
        finally:
            await async_mongo_client.close()

    return asyncio.run(_main())


async def _round_status(database, round_id: str) -> dict:
    round_service = build_round_service(database, get_settings())
    publishing_round = await round_service.get(round_id)
    if publishing_round is None:
        typer.echo(f"라운드 {round_id} 가 없다.", err=True)
        raise typer.Exit(1)
    calls = AgentCallRepository(database)
    drafts = []
    for draft in await PostDraftRepository(database).list_by_round(round_id):
        usage = await calls.sum_usage(draft.draft_id)
        drafts.append(
            {
                "draft_id": draft.draft_id,
                "status": draft.status,
                "iteration": draft.iteration,
                "versions": len(draft.versions),
                "title": draft.current_version.title if draft.versions else None,
                "escalation_reason": draft.escalation_reason,
                "failure": draft.failure.code if draft.failure else None,
                "tokens": usage.total_tokens,
            }
        )
    return {
        "round_id": round_id,
        "status": publishing_round.status,
        "abort_reason": publishing_round.abort_reason,
        "topic_attempts": len(publishing_round.topic_attempts),
        "drafts": drafts,
    }


def _is_waiting_on_human(status: dict) -> bool:
    """러너가 더 할 일이 없는지: 라운드가 끝났거나, 글이 모두 승인·사람 결정 대기·종결이다."""
    if status["status"] in (RoundStatus.COMPLETED, RoundStatus.ABORTED):
        return True
    if status["status"] == RoundStatus.TOPIC_SELECTION or not status["drafts"]:
        return False
    halted = {"READY_TO_PUBLISH", "ESCALATED", "FAILED", "PUBLISHED", "REJECTED"}
    return all(draft["status"] in halted for draft in status["drafts"])


@agents.command()
def start_round(
    theme: Annotated[str | None, typer.Option(help="세 슬롯 모두 이 범위 안에서 주제를 제안한다")] = None,
    wait: Annotated[
        bool, typer.Option(help="러너가 더 할 일이 없을 때(종결·사람 대기)까지 폴링하며 상태를 찍는다")
    ] = False,
) -> None:
    """Post BE 카테고리를 읽어 라운드를 TOPIC_SELECTION 으로 만든다. 진행은 러너(서버 또는 `agents run`)가 한다."""

    async def _start(database, settings):
        publishing_round = await build_round_service(database, settings).start_round(
            theme_keyword=theme, slot_bindings=settings.agent_slot_bindings
        )
        typer.echo(
            json.dumps({"round_id": publishing_round.round_id, "status": publishing_round.status}, ensure_ascii=False)
        )
        while wait:
            await asyncio.sleep(settings.scan_interval_s)
            status = await _round_status(database, publishing_round.round_id)
            typer.echo(json.dumps(status, ensure_ascii=False))
            if _is_waiting_on_human(status):
                break

    _run_agents(_start)


@agents.command()
def status(round_id: str) -> None:
    """라운드와 글 3편의 상태·iteration·토큰."""

    async def _status(database, settings):
        typer.echo(json.dumps(await _round_status(database, round_id), ensure_ascii=False, indent=2))

    _run_agents(_status)


@agents.command()
def abort(round_id: str) -> None:
    """라운드를 중단한다. PUBLISHED 외 글은 REJECTED, 레지스트리는 RELEASED 다."""

    async def _abort(database, settings):
        await build_round_service(database, settings).abort_round(round_id)
        typer.echo(f"라운드 {round_id} 를 중단했다.")

    _run_agents(_abort)


def _echo_draft(draft) -> None:
    typer.echo(json.dumps(DraftPublic.from_draft(draft).model_dump(mode="json"), ensure_ascii=False, indent=2))


@agents.command()
def show(
    draft_id: str,
    version: Annotated[int | None, typer.Option(help="본문을 찍을 버전 번호. 없으면 마지막 버전")] = None,
) -> None:
    """글의 버전·리뷰·중재·결정과 한 버전의 본문을 찍는다."""

    async def _show(database, settings):
        draft = await PostDraftRepository(database).get(draft_id)
        if draft is None:
            typer.echo(f"글 {draft_id} 가 없다.", err=True)
            raise typer.Exit(1)
        _echo_draft(draft)
        for each in draft.versions:
            typer.echo(
                f"-- v{each.version_number} {each.title!r} passed={each.passed_validation} origin={each.origin_cause} "
                f"errors={each.validation_errors} resolutions={[(r.item_id, r.status) for r in each.resolutions]}"
            )
        for review in draft.reviews:
            typer.echo(f"-- review {review.review_id} {review.derived_verdict}: {review.overall_feedback}")
            for item in review.items:
                typer.echo(f"   [{item.severity}] {item.item_id} ({item.section_id}) {item.comment}")
        for arbitration in draft.arbitrations:
            typer.echo(f"-- arbitration {arbitration.item_id}: {arbitration.outcome} — {arbitration.reasoning}")
        if draft.publication_intent:
            typer.echo(f"-- intent {draft.publication_intent.model_dump(mode='json')}")
        if draft.publication:
            typer.echo(f"-- publication {draft.publication.model_dump(mode='json', exclude={'peer_review_note'})}")
        chosen = (
            next((each for each in draft.versions if each.version_number == version), draft.versions[-1])
            if draft.versions
            else None
        )
        if chosen is not None:
            typer.echo(f"\n===== v{chosen.version_number} {chosen.title} =====\n{chosen.content_markdown}")

    _run_agents(_show)


@agents.command()
def resolve(
    draft_id: str,
    decision: Annotated[
        EscalationDecisionKind, typer.Option(help="PUBLISH_AS_IS | REVISE | REJECT | CONFIRM_PUBLISHED")
    ],
    decided_by: Annotated[str, typer.Option()] = "cli",
    instruction: Annotated[str | None, typer.Option(help="REVISE 의 지시문")] = None,
    external_post_id: Annotated[str | None, typer.Option(help="CONFIRM_PUBLISHED: 블로그에서 찾은 게시글 id")] = None,
) -> None:
    """ESCALATED 글을 해소한다. PUBLISH_UNCERTAIN 이면 블로그를 먼저 확인한다."""

    async def _resolve(database, settings):
        draft = await build_draft_decision_service(database, settings).resolve_escalation(
            draft_id, decision, decided_by=decided_by, instruction=instruction, external_post_id=external_post_id
        )
        _echo_draft(draft)

    _run_agents(_resolve)


@agents.command()
def approve(draft_id: str, approved_by: Annotated[str, typer.Option()] = "cli") -> None:
    """READY_TO_PUBLISH 글의 발행을 승인한다. 다음 스캔이 발행한다."""

    async def _approve(database, settings):
        _echo_draft(await build_draft_decision_service(database, settings).approve(draft_id, approved_by=approved_by))

    _run_agents(_approve)


@agents.command()
def retry(
    draft_id: str,
    token_limit: Annotated[int | None, typer.Option(min=1, help="새 소프트 상한. 현재 사용량보다 커야 한다")] = None,
) -> None:
    """FAILED 글을 실패한 단계부터 다시 돌린다."""

    async def _retry(database, settings):
        _echo_draft(await build_draft_decision_service(database, settings).retry(draft_id, token_limit=token_limit))

    _run_agents(_retry)


@agents.command()
def discard(draft_id: str, decided_by: Annotated[str, typer.Option()] = "cli") -> None:
    """FAILED 글 하나만 폐기한다. 레지스트리는 RELEASED 다."""

    async def _discard(database, settings):
        _echo_draft(await build_draft_decision_service(database, settings).discard(draft_id, decided_by=decided_by))

    _run_agents(_discard)


@agents.command()
def run() -> None:
    """서버 없이 이 프로세스에서 러너를 돌린다. 디버깅용. 서버와 동시에 띄우지 않는다(같은 글을 두 프로세스가 돌린다). Ctrl+C 로 끝낸다."""

    async def _run(database, settings) -> None:
        await ensure_agent_indexes(database)
        runner = build_pipeline_runner(database, settings)
        runner.start()
        try:
            await asyncio.Event().wait()
        finally:
            await runner.stop()

    try:
        _run_agents(_run)
    except KeyboardInterrupt:
        typer.echo("러너를 멈췄다.")


if __name__ == "__main__":
    cli()
