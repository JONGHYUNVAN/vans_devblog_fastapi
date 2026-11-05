"""운영 명령. 사용법: python -m cli --help (Django 관리 명령 sync_posts_to_elasticsearch·setup_popular_searches 대체)."""

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from app.core.config import get_settings
from app.core.exceptions import InvalidRequestError, NotFoundError
from app.dependencies import (
    build_agent_call_service,
    build_code_graph_service,
    build_draft_decision_service,
    build_pipeline_runner,
    build_pipeline_view_service,
    build_round_service,
    build_service_graph_service,
    ensure_agent_indexes,
    ensure_code_graph_indexes,
    get_index_setup_service,
    get_sync_service,
    open_async_database,
    open_clients,
)
from app.models.enums import EscalationDecisionKind, RoundStatus
from app.models.model_binding import CallModel
from app.schemas.agent_pipeline import RoundProgress
from app.schemas.sync import SyncOptions
from app.services.pipeline_views import build_draft_public

cli = typer.Typer(help="VansDevBlog 검색 서비스 운영 명령", no_args_is_help=True)
agents = typer.Typer(help="에이전트 파이프라인 (설계 agents_design/06 문서 3절 cli)", no_args_is_help=True)
cli.add_typer(agents, name="agents")
code_graph = typer.Typer(help="코드 그래프 (code_graph_design/README.md)", no_args_is_help=True)
cli.add_typer(code_graph, name="code-graph")


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
    """비동기 Mongo 연결을 열고 코루틴을 돌린 뒤 닫는다. agents 명령이 같이 쓴다. 없는 id 와 잘못된 값은 메시지와 종료 코드 1."""
    settings = get_settings()
    if not settings.mongodb_db_name:
        typer.echo("MongoDB DB 이름(MONGODB_DATABASE 또는 URI 경로)이 없다.", err=True)
        raise typer.Exit(1)

    async def _main():
        async with open_async_database(settings) as database:
            return await coroutine_factory(database, settings)

    try:
        return asyncio.run(_main())
    except (NotFoundError, InvalidRequestError) as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(1) from exc


def _echo(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _is_waiting_on_human(progress: RoundProgress) -> bool:
    """러너가 더 할 일이 없는지: 라운드가 끝났거나, 글이 모두 승인 대기·사람 결정 대기·종결이다.

    READY_TO_PUBLISH 는 승인을 기다릴 때만 멈춘 상태다. 승인됐거나 토글이 꺼져 있으면 러너가 발행 중이다.
    """
    if progress.status in (RoundStatus.COMPLETED, RoundStatus.ABORTED):
        return True
    if progress.status == RoundStatus.TOPIC_SELECTION or not progress.drafts:
        return False
    halted = {"ESCALATED", "FAILED", "PUBLISHED", "REJECTED"}
    return all(draft.status in halted or draft.awaits_approval for draft in progress.drafts)


@agents.command()
def start_round(
    theme: Annotated[str | None, typer.Option(help="세 슬롯 모두 이 범위 안에서 주제를 제안한다")] = None,
    wait: Annotated[
        bool, typer.Option(help="러너가 더 할 일이 없을 때(종결·사람 대기)까지 폴링하며 상태를 찍는다")
    ] = False,
    evidence_file: Annotated[
        Path | None,
        typer.Option(
            help="실제 프로젝트 근거 파일(git 이력, code-graph context, 측정 기록). 없으면 400 을 고를 수 없다"
        ),
    ] = None,
) -> None:
    """Post BE 카테고리를 읽어 라운드를 TOPIC_SELECTION 으로 만든다. 진행은 러너(서버 또는 `agents run`)가 한다."""

    async def _start(database, settings):
        publishing_round = await build_round_service(database, settings).start_round(
            theme_keyword=theme, evidence=evidence_file.read_text(encoding="utf-8") if evidence_file else None
        )
        typer.echo(
            json.dumps({"round_id": publishing_round.round_id, "status": publishing_round.status}, ensure_ascii=False)
        )
        views = build_pipeline_view_service(database, settings)
        while wait:
            await asyncio.sleep(settings.scan_interval_s)
            progress = await views.get_round_progress(publishing_round.round_id)
            typer.echo(progress.model_dump_json())
            if _is_waiting_on_human(progress):
                break

    _run_agents(_start)


@agents.command()
def status(round_id: str) -> None:
    """라운드와 글 3편의 상태·승인 대기 여부·iteration·토큰."""

    async def _status(database, settings):
        progress = await build_pipeline_view_service(database, settings).get_round_progress(round_id)
        _echo(progress.model_dump(mode="json"))

    _run_agents(_status)


@agents.command()
def abort(round_id: str) -> None:
    """라운드를 중단한다. PUBLISHED 외 글은 REJECTED, 레지스트리는 RELEASED 다."""

    async def _abort(database, settings):
        await build_round_service(database, settings).abort_round(round_id)
        typer.echo(f"라운드 {round_id} 를 중단했다.")

    _run_agents(_abort)


@agents.command()
def show(
    draft_id: str,
    version: Annotated[int | None, typer.Option(help="본문을 찍을 버전 번호. 없으면 마지막 버전")] = None,
) -> None:
    """글의 버전·리뷰·중재·결정과 한 버전의 본문을 찍는다."""

    async def _show(database, settings):
        history = await build_pipeline_view_service(database, settings).get_draft_history(draft_id)
        _echo(
            history.model_dump(
                mode="json",
                exclude={"topic", "reviewer_assignments", "versions", "reviews", "arbitrations", "publication"},
            )
        )
        for each in history.versions:
            typer.echo(
                f"-- v{each.version_number} {each.title!r} passed={each.passed_validation} origin={each.origin_cause} "
                f"errors={each.validation_errors} resolutions={[(r.item_id, r.status) for r in each.resolutions]}"
            )
        for review in history.reviews:
            typer.echo(f"-- review {review.review_id} {review.derived_verdict}: {review.overall_feedback}")
            for item in review.items:
                typer.echo(f"   [{item.severity}] {item.item_id} ({item.section_id}) {item.comment}")
        for arbitration in history.arbitrations:
            typer.echo(f"-- arbitration {arbitration.item_id}: {arbitration.outcome} — {arbitration.reasoning}")
        if history.polish:
            typer.echo(f"-- polish {history.polish.model_dump(mode='json')}")
        if history.final_read:
            typer.echo(f"-- final read v{history.final_read.target_version}: {history.final_read.overall}")
            for finding in history.final_read.findings:
                typer.echo(f"   [{finding.kind}] ({finding.section_id}) {finding.comment} «{finding.quote}»")
        if history.level_assessment:
            typer.echo(f"-- level {history.level_assessment.level}: {history.level_assessment.reasoning}")
        if history.publication:
            typer.echo(f"-- publication {history.publication.model_dump(mode='json', exclude={'peer_review_note'})}")
        chosen = (
            next((each for each in history.versions if each.version_number == version), history.versions[-1])
            if history.versions
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
        _echo(build_draft_public(draft).model_dump(mode="json"))

    _run_agents(_resolve)


@agents.command()
def approve(draft_id: str, approved_by: Annotated[str, typer.Option()] = "cli") -> None:
    """READY_TO_PUBLISH 글의 발행을 승인한다. 다음 스캔이 발행한다. 토글이 꺼져 있거나 이미 승인됐으면 거절한다."""

    async def _approve(database, settings):
        draft = await build_draft_decision_service(database, settings).approve(draft_id, approved_by=approved_by)
        _echo(build_draft_public(draft).model_dump(mode="json"))

    _run_agents(_approve)


@agents.command()
def retry(
    draft_id: str,
    token_limit: Annotated[int | None, typer.Option(min=1, help="새 소프트 상한. 현재 사용량보다 커야 한다")] = None,
) -> None:
    """FAILED 글을 실패한 단계부터 다시 돌린다."""

    async def _retry(database, settings):
        draft = await build_draft_decision_service(database, settings).retry(draft_id, token_limit=token_limit)
        _echo(build_draft_public(draft).model_dump(mode="json"))

    _run_agents(_retry)


@agents.command()
def discard(draft_id: str, decided_by: Annotated[str, typer.Option()] = "cli") -> None:
    """FAILED 글 하나만 폐기한다. 레지스트리는 RELEASED 다."""

    async def _discard(database, settings):
        draft = await build_draft_decision_service(database, settings).discard(draft_id, decided_by=decided_by)
        _echo(build_draft_public(draft).model_dump(mode="json"))

    _run_agents(_discard)


@agents.command()
def replay(
    call_id: str,
    provider: Annotated[str | None, typer.Option(help="다른 벤더로 보낸다. --model 과 함께")] = None,
    model: Annotated[str | None, typer.Option(help="다른 모델 ID 로 보낸다")] = None,
) -> None:
    """영수증의 메시지를 그대로 다시 보내 응답을 찍는다(프롬프트 튜닝용). 영수증·상태는 바꾸지 않는다."""

    async def _replay(database, settings):
        service = build_agent_call_service(database, settings)
        call = await service.get(call_id)
        binding = call.model_binding.model_copy(
            update={key: value for key, value in (("provider", provider), ("model_id", model)) if value}
        )
        result = await service.replay(call, binding=binding)
        _echo(
            {
                "call_id": call_id,
                "kind": call.kind,
                "model": f"{binding.provider}/{binding.model_id}",
                "parsed_ok": result.parsed_ok,
                "error": result.error,
                "usage": result.usage.model_dump(),
                "latency_ms": result.latency_ms,
            }
        )
        typer.echo(f"\n===== 응답 =====\n{result.text}")

    _run_agents(_replay)


@agents.command()
def compare_prompts(
    call_id: str,
    version: Annotated[list[str], typer.Option(help="비교할 프롬프트 버전. 여러 번 준다")],
    core_mechanism: Annotated[str, typer.Option(help="옛 입력에 없는 핵심 메커니즘")],
    out_of_scope: Annotated[list[str], typer.Option(help="옛 입력에 없는 '다루지 않을 것'. 여러 번 준다")],
    runs: Annotated[int, typer.Option(min=1, help="버전마다 몇 번 쓸지")] = 3,
) -> None:
    """저장된 WRITE_DRAFT 입력을 프롬프트 버전별로 다시 써 밀도를 비교한다. 영수증·상태는 바꾸지 않는다."""

    async def _compare(database, settings):
        service = build_agent_call_service(database, settings)
        rows = await service.compare_write_prompts(
            await service.get(call_id),
            versions=version,
            runs=runs,
            core_mechanism=core_mechanism,
            out_of_scope=out_of_scope,
        )
        for prompt_version, run, density, error in rows:
            _echo(
                {
                    "version": prompt_version,
                    "run": run,
                    "error": error,
                    "density": density.model_dump(mode="json") if density else None,
                }
            )

    _run_agents(_compare)


@agents.command()
def fact_check(
    file: Path,
    provider: Annotated[str, typer.Option(help="검색 호출 벤더")],
    model: Annotated[str, typer.Option(help="검색 호출 모델")],
    effort: Annotated[str | None, typer.Option(help="low | medium | high | xhigh | max")] = None,
    transform_provider: Annotated[str, typer.Option(help="JSON 변환 벤더")] = "anthropic",
    transform_model: Annotated[str, typer.Option(help="JSON 변환 모델")] = "claude-haiku-5-5",
    max_output_tokens: Annotated[int, typer.Option(min=1)] = 16000,
) -> None:
    """마크다운 파일 하나를 사실 확인해 주장을 찍는다. 잘못된 API 를 심은 글로 재현율·오탐을 잰다. 영수증·상태는 바꾸지 않는다."""

    async def _fact_check(database, settings):
        claims, citations = await build_agent_call_service(database, settings).probe_fact_check(
            file.read_text(encoding="utf-8"),
            binding=CallModel(provider=provider, model_id=model, effort=effort, max_output_tokens=max_output_tokens),
            transformer=CallModel(provider=transform_provider, model_id=transform_model, max_output_tokens=8000),
            prompt_version=settings.prompt_version,
        )
        _echo(
            {
                "claims": [claim.model_dump(mode="json") for claim in claims],
                "citations": [citation.url for citation in citations],
            }
        )

    _run_agents(_fact_check)


@agents.command()
def seed_routes(file: Path) -> None:
    """호출별 모델 라우트(JSON 배열, 예시는 agents_design/routes.example.json)를 넣는다. 진행 중인 라운드는 스냅샷을 쓴다."""

    async def _seed(database, settings):
        documents = json.loads(file.read_text(encoding="utf-8"))
        await build_round_service(database, settings).seed_routes(documents)
        typer.echo(f"라우트 문서 {len(documents)}건을 넣었다.")

    try:
        _run_agents(_seed)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@agents.command()
def routes() -> None:
    """현재 라우트 문서를 찍는다."""

    async def _routes(database, settings):
        _echo(await build_round_service(database, settings).list_route_documents())

    _run_agents(_routes)


@agents.command()
def label(draft_id: str, level: Annotated[int, typer.Argument(help="100 | 200 | 300 | 400")]) -> None:
    """사용자가 매긴 수준을 글에 남긴다. `agents metrics` 의 일치도 측정에 쓴다. 흐름은 바꾸지 않는다."""

    async def _label(database, settings):
        await build_draft_decision_service(database, settings).label_level(draft_id, level)
        typer.echo(f"{draft_id} 를 {level} 로 표시했다.")

    _run_agents(_label)


@agents.command()
def metrics(round_id: Annotated[str | None, typer.Option(help="없으면 모든 라운드")] = None) -> None:
    """운영 지표(설계 07 문서 4절): fast-track 비율, 평균 iteration, 격리·실패 사유, 판정 불일치, 게이트 실패, 글당 토큰."""

    async def _metrics(database, settings):
        result = await build_pipeline_view_service(database, settings).compute_metrics(round_id)
        _echo(result.model_dump(mode="json"))

    _run_agents(_metrics)


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


def _run_now(function: Callable[..., Any], *args: Any) -> None:
    """CLI 에는 응답이 없으므로 백그라운드로 넘길 분석을 그 자리에서 돌린다."""
    function(*args)


@code_graph.command("analyze")
def analyze_code_graph(repo: str) -> None:
    """GitHub tarball 을 받아 파일 그래프·진단을 만들고 Mongo 에 저장한다. 같은 commit 이 이미 있으면 저장된 것을 쓴다.

    저장하지 못하면(경고 로그 참고) 종료 코드 1.
    """
    settings = get_settings()
    with open_clients(settings) as (_, mongo_client):
        ensure_code_graph_indexes(mongo_client[settings.mongodb_db_name])
        service = build_code_graph_service(mongo_client, settings, run_in_background=_run_now)
        try:
            analysis = service.analyze_repo(repo)
        except NotFoundError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(1) from exc
        latest = next((each for each in service.list_code_graphs() if each.repo == repo), None)
    if latest is None or (analysis.status == "queued" and latest.commit_sha != analysis.commit_sha):
        typer.echo(f"{repo}@{analysis.commit_sha[:7]} 분석을 저장하지 못했다.", err=True)
        raise typer.Exit(1)
    _echo({"status": analysis.status, **latest.model_dump(mode="json")})


@code_graph.command("context")
def code_graph_context(repo: str) -> None:
    """저장된 최신 분석의 context 마크다운을 찍는다(CLAUDE.md 갱신용)."""
    settings = get_settings()
    with open_clients(settings) as (_, mongo_client):
        try:
            context = build_code_graph_service(
                mongo_client, settings, run_in_background=_run_now
            ).get_code_graph_context(repo)
        except NotFoundError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(1) from exc
    typer.echo(context, nl=False)


@code_graph.command("flow")
def code_graph_flow(
    flow: str,
    mermaid: Annotated[bool, typer.Option("--mermaid", help="mermaid sequenceDiagram 텍스트만 찍는다")] = False,
) -> None:
    """저장된 호출 지점과 OpenAPI 로 e2e 흐름(search·publish·posts·auth)을 hop 마다 찍는다."""
    from app.main import app  # 이 앱의 OpenAPI 는 HTTP 없이 app.openapi 로 받는다 (G-13)

    settings = get_settings()
    with open_clients(settings) as (_, mongo_client):
        try:
            result = build_service_graph_service(mongo_client, settings, self_openapi=app.openapi).get_flow(flow)
        except NotFoundError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(1) from exc
    if mermaid:
        typer.echo(result.mermaid, nl=False)
        return
    _echo(result.model_dump(mode="json", exclude={"mermaid"}))


if __name__ == "__main__":
    cli()
