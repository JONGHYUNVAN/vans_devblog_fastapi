"""운영 명령. 사용법: python -m cli --help (Django 관리 명령 sync_posts_to_elasticsearch·setup_popular_searches 대체).

검색(sync-posts·setup-popular), 코드 그래프(code-graph), 에이전트 파이프라인(agents …) 명령이 한 진입점에 있다. 연결은
dependencies.open_clients·open_async_database 로 직접 열고 라우터와 같은 build_*·get_* 로 서비스를 조립한다(같은 조립, 다른 진입점).
`agents pipeline …` 은 버전 문서의 넣기 쪽 명령만 둔다(09 문서 5절, Q-40): 조회·되돌리기 명령은 없고 되돌리기는 옛 내보내기의 import 다.
하위 Typer 는 모두 등록한 뒤 맨 끝에서 cli() 를 부른다. 등록보다 먼저 부르면 뒤에 add_typer 한 그룹이 "No such command" 다.
"""

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
    build_code_graph_repo_config_service,
    build_code_graph_service,
    build_derived_records_writer,
    build_draft_decision_service,
    build_glossary_service,
    build_glossary_view_service,
    build_pipeline_runner,
    build_pipeline_version_service,
    build_pipeline_view_service,
    build_provenance_service,
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
from app.models.pipeline import PromptSet, content_hash_of
from app.schemas.agent_pipeline import RoundProgress
from app.schemas.sync import SyncOptions
from app.services.pipeline_validation import errors_of, validate_version
from app.services.pipeline_versions import (
    DEFAULT_PROMPTS_DIR,
    PipelineVersionService,
    build_baseline_version,
    describe,
    export_version,
    meta_of,
    prompts_from_reference,
    read_directory,
)
from app.services.pipeline_views import build_draft_public

cli = typer.Typer(help="VansDevBlog 검색 서비스 운영 명령", no_args_is_help=True)
agents = typer.Typer(help="에이전트 파이프라인 (설계 agents_design/06 문서 3절 cli)", no_args_is_help=True)
cli.add_typer(agents, name="agents")
code_graph = typer.Typer(help="코드 그래프 (code_graph_design/README.md)", no_args_is_help=True)
cli.add_typer(code_graph, name="code-graph")


@cli.callback()
def configure_logging() -> None:
    """모든 명령 앞에 로깅을 켠다. ES 클라이언트의 요청 로그는 WARNING 이상만 남긴다."""
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
    pipeline_version: Annotated[
        str | None,
        typer.Option(help="최신이 아닌 파이프라인 버전으로 돌린다(차기 프롬프트 시험, 09 문서 Q-42). 없으면 최신"),
    ] = None,
) -> None:
    """Post BE 카테고리를 읽어 라운드를 TOPIC_SELECTION 으로 만든다. 진행은 러너(서버 또는 `agents run`)가 한다."""

    async def _start(database, settings):
        publishing_round = await build_round_service(database, settings).start_round(
            theme_keyword=theme,
            evidence=evidence_file.read_text(encoding="utf-8") if evidence_file else None,
            pipeline_version_id=pipeline_version,
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
    runs: Annotated[int, typer.Option(min=1, help="버전마다 몇 번 쓸지")] = 3,
    core_mechanism: Annotated[
        str | None, typer.Option(help="옛 입력에 없을 때 채울 핵심 메커니즘. 입력에 있으면 생략")
    ] = None,
    out_of_scope: Annotated[
        list[str] | None, typer.Option(help="옛 입력에 없을 때 채울 '다루지 않을 것'. 여러 번 준다. 입력에 있으면 생략")
    ] = None,
    out_dir: Annotated[
        Path | None, typer.Option(help="생성한 글을 {버전}-{회차}.md 로 저장할 디렉터리. 읽고 판단하는 용도")
    ] = None,
    max_output_tokens: Annotated[
        int | None,
        typer.Option(
            min=1, help="영수증의 출력 한도 대신 쓸 값. 생각 토큰이 한도를 다 써 본문이 비면 올려서 다시 돈다"
        ),
    ] = None,
) -> None:
    """저장된 WRITE_DRAFT 입력을 프롬프트 집합별로 다시 써 밀도를 비교한다. 영수증·상태는 바꾸지 않는다. 가족은 영수증의 모델을 따른다.

    --version 은 파이프라인 버전 id(v3), 내보내기 디렉터리(pipeline/next/), 저장소 프롬프트 디렉터리 이름(v4) 중 하나다. 들여오지 않고 써 볼 수 있다.
    """

    def _save(label: str, run: int, output) -> None:
        if out_dir is None:
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{label.replace('/', '_').replace(chr(92), '_')}-{run}.md"
        path.write_text(f"# {output.title}\n\n> {output.summary}\n\n{output.content_markdown}", encoding="utf-8")
        typer.echo(f"saved {path}")

    async def _compare(database, settings):
        service = build_agent_call_service(database, settings)
        prompt_sets = {
            reference: await _prompts_of(build_pipeline_version_service(database), reference) for reference in version
        }
        rows = await service.compare_write_prompts(
            await service.get(call_id),
            prompt_sets=prompt_sets,
            runs=runs,
            core_mechanism=core_mechanism,
            out_of_scope=out_of_scope,
            on_draft=_save,
            max_output_tokens=max_output_tokens,
        )
        for label, run, density, error in rows:
            _echo(
                {
                    "version": label,
                    "run": run,
                    "error": error,
                    "density": density.model_dump(mode="json") if density else None,
                }
            )

    _run_agents(_compare)


async def _prompts_of(service: PipelineVersionService, reference: str | None) -> PromptSet:
    """프롬프트 집합 참조 → 템플릿. None 이면 최신 버전, 버전 id 면 그 버전, 아니면 디렉터리(내보내기 또는 저장소 프롬프트)다."""
    if reference is None:
        return (await service.require_latest()).prompts
    try:
        return (await service.get(reference)).prompts
    except NotFoundError:
        return prompts_from_reference(reference)


@agents.command()
def fact_check(
    file: Path,
    provider: Annotated[str, typer.Option(help="검색 호출 벤더")],
    model: Annotated[str, typer.Option(help="검색 호출 모델")],
    effort: Annotated[str | None, typer.Option(help="low | medium | high | xhigh | max")] = None,
    transform_provider: Annotated[str, typer.Option(help="JSON 변환 벤더")] = "anthropic",
    transform_model: Annotated[str, typer.Option(help="JSON 변환 모델")] = "claude-haiku-5-5",
    max_output_tokens: Annotated[int, typer.Option(min=1)] = 16000,
    pipeline: Annotated[
        str | None, typer.Option(help="프롬프트를 가져올 파이프라인 버전 id 또는 디렉터리. 없으면 최신 버전")
    ] = None,
) -> None:
    """마크다운 파일 하나를 사실 확인해 주장을 찍는다. 잘못된 API 를 심은 글로 재현율·오탐을 잰다. 영수증·상태는 바꾸지 않는다."""

    async def _fact_check(database, settings):
        claims, citations = await build_agent_call_service(database, settings).probe_fact_check(
            file.read_text(encoding="utf-8"),
            binding=CallModel(provider=provider, model_id=model, effort=effort, max_output_tokens=max_output_tokens),
            transformer=CallModel(provider=transform_provider, model_id=transform_model, max_output_tokens=8000),
            prompts=await _prompts_of(build_pipeline_version_service(database), pipeline),
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
    """옛 agent_routes 에 라우트(JSON 배열, 예시는 agents_design/routes.example.json)를 넣는다.

    라운드는 이제 파이프라인 버전 문서의 routes 를 쓴다(09 문서). 라우트를 바꾸려면 `agents pipeline edit-routes` 다.
    이 명령은 `agents pipeline bootstrap --routes mongo` 의 입력으로만 남는다.
    """

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


@code_graph.command("seed-repos")
def seed_code_graph_repos(file: Path) -> None:
    """분석 대상 저장소 설정(JSON 객체, 예시는 code_graph_design/repos.example.json)을 Mongo 에 넣는다. 파일에 없는 저장소는 지운다."""
    settings = get_settings()
    try:
        repos = json.loads(file.read_text(encoding="utf-8"))
        with open_clients(settings) as (_, mongo_client):
            build_code_graph_repo_config_service(mongo_client, settings).seed(repos)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"저장소 설정 {len(repos)}건을 넣었다: {', '.join(sorted(repos))}")


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
        progress = service.get_analysis_status(repo)
    if latest is None or progress.state == "failed":
        typer.echo(f"{repo}@{analysis.commit_sha[:7]} 분석을 저장하지 못했다: {progress.error}", err=True)
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


@code_graph.command("impact")
def code_graph_impact(
    repo: str,
    file: str,
    hops: Annotated[int, typer.Option(min=1, max=5, help="따라갈 깊이")] = 1,
    runtime_only: Annotated[bool, typer.Option(help="타입으로만 쓰는 import 를 따라가지 않는다")] = False,
) -> None:
    """파일 하나를 고칠 때의 영향 반경: 이 파일을 쓰는 쪽(upstream)과 이 파일이 쓰는 쪽(downstream)."""
    settings = get_settings()
    with open_clients(settings) as (_, mongo_client):
        try:
            result = build_code_graph_service(mongo_client, settings, run_in_background=_run_now).get_impact(
                repo, file, hops, runtime_only=runtime_only
            )
        except NotFoundError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(1) from exc
    _echo(result.model_dump(mode="json", exclude={"edges"}))


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


# ---------- 파이프라인 버전 (설계 09 문서 5절) ----------

pipeline = typer.Typer(
    help="파이프라인 버전: 프롬프트·라우트·페르소나·조절 값을 Mongo 버전 문서로 넣는다 (설계 agents_design/09 문서)",
    no_args_is_help=True,
)
agents.add_typer(pipeline, name="pipeline")


def _run_pipeline(coroutine_factory):
    """PipelineVersionService 를 조립해 코루틴을 돌린다. 검증 실패·잘못된 입력(ValueError)은 메시지와 종료 코드 1."""

    async def _main(database, settings):
        return await coroutine_factory(build_pipeline_version_service(database), database, settings)

    try:
        return _run_agents(_main)
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def _echo_findings(findings) -> bool:
    """검증 결과를 찍고 오류가 있으면 True."""
    for line in describe(findings):
        typer.echo(line, err=True)
    return bool(errors_of(findings))


@pipeline.command("bootstrap")
def pipeline_bootstrap(
    routes: Annotated[str, typer.Option(help="라우트 출처: mongo(옛 agent_routes) 또는 JSON 파일 경로")] = "mongo",
    prompts_dir: Annotated[
        str, typer.Option(help="저장소 프롬프트 디렉터리 이름(app/agents/prompts/ 아래) 또는 경로")
    ] = DEFAULT_PROMPTS_DIR,
    note: Annotated[str, typer.Option()] = "",
    dry_run: Annotated[bool, typer.Option(help="넣지 않고 검증 결과와 해시만 찍는다")] = False,
) -> None:
    """첫 버전 v1 을 만든다(이행 1a). 지금 라우트·저장소 프롬프트·코드 상수를 그대로 옮긴다. 이미 버전이 있으면 거절한다."""

    async def _bootstrap(service: PipelineVersionService, database, settings):
        # 1) 라우트 출처. 운영은 옛 agent_routes(mongo)이고 파일은 새 환경·테스트용이다(routes.example.json 형식)
        documents = (
            await build_round_service(database, settings).list_route_documents()
            if routes == "mongo"
            else json.loads(Path(routes).read_text(encoding="utf-8"))
        )
        # 2) dry-run 은 본문을 만들어 해시·검증 결과만 찍는다. 운영 투입 전에 해시를 pipeline/baseline 과 견주는 용도다
        if dry_run:
            body = build_baseline_version(prompts_dir, documents)
            findings = validate_version(body)
            _echo(
                {
                    "dry_run": True,
                    "content_hash": content_hash_of(body),
                    "routes": sorted(body.routes),
                    "prompt_families": {family: sorted(templates) for family, templates in body.prompts.items()},
                    "parameters": body.parameters.model_dump(mode="json"),
                }
            )
            return _echo_findings(findings)
        # 3) 실제 시드. 이미 버전이 있으면 서비스가 ValueError 로 거절한다(종료 코드 1)
        version = await service.bootstrap(route_documents=documents, prompts_dir=prompts_dir, note=note)
        _echo(meta_of(version))
        return False

    if _run_pipeline(_bootstrap):
        raise typer.Exit(1)


@pipeline.command("validate")
def pipeline_validate(
    target: Annotated[str, typer.Argument(help="내보내기 디렉터리 또는 버전 id")],
    all_referenced: Annotated[
        bool, typer.Option("--all-referenced", help="최신 버전과 진행 중 라운드가 가리키는 버전도 함께 검증한다(R-12)")
    ] = False,
) -> None:
    """09 문서 4절 규칙으로 검증한다. 오류가 있으면 종료 코드 1."""

    async def _validate(service: PipelineVersionService, database, settings):
        failed = False
        if Path(target).is_dir():
            body = read_directory(target)
            typer.echo(f"{target}: {content_hash_of(body)}")
            failed |= _echo_findings(validate_version(body))
        else:
            version = await service.get(target)
            typer.echo(f"{version.version_id}: {version.content_hash}")
            failed |= _echo_findings(validate_version(version.body(), expected_hash=version.content_hash))
        # --all-referenced: 배포 전 점검(R-12). 최신 버전과 진행 중 라운드가 고정한 버전이 지금 코드의 규칙으로도 유효한지 본다
        if all_referenced:
            seen: set[str] = set()
            latest = await service.latest()
            referenced = [latest] if latest is not None else []
            round_service = build_round_service(database, settings)
            for status in (RoundStatus.TOPIC_SELECTION, RoundStatus.IN_PROGRESS):
                for publishing_round in await round_service.list_rounds(status):
                    referenced.append(await service.for_round(publishing_round))
            for version in referenced:
                if version.version_id in seen:
                    continue
                seen.add(version.version_id)
                typer.echo(f"{version.version_id}: {version.content_hash}")
                failed |= _echo_findings(validate_version(version.body(), expected_hash=version.content_hash))
        return failed

    if _run_pipeline(_validate):
        raise typer.Exit(1)


@pipeline.command("export")
def pipeline_export(
    out_dir: Annotated[Path, typer.Option(help="pipeline.json 과 prompts/ 를 쓸 디렉터리(예: pipeline/baseline)")],
    version: Annotated[str | None, typer.Argument(help="버전 id. 없으면 최신")] = None,
) -> None:
    """버전을 디렉터리로 내보낸다. git 으로 리뷰·보관하는 형식이다."""

    async def _export(service: PipelineVersionService, database, settings):
        target = await service.require_latest() if version is None else await service.get(version)
        for path in export_version(target, out_dir, meta=meta_of(target)):
            typer.echo(str(path))
        return False

    _run_pipeline(_export)


@pipeline.command("import")
def pipeline_import(
    directory: Annotated[Path, typer.Argument(help="내보내기 디렉터리")],
    note: Annotated[str, typer.Option()] = "",
    label: Annotated[str | None, typer.Option()] = None,
) -> None:
    """디렉터리를 읽어 검증하고 새 버전으로 넣는다. 부모는 최신이다. 최신과 같은 해시면 거절한다. 다음 라운드부터 쓰인다."""

    async def _import(service: PipelineVersionService, database, settings):
        _echo(meta_of(await service.import_directory(directory, note=note, label=label)))
        return False

    _run_pipeline(_import)


@pipeline.command("edit-prompt")
def pipeline_edit_prompt(
    family: Annotated[str, typer.Argument(help="claude | gemini | grok | haiku | common")],
    name: Annotated[str, typer.Argument(help="템플릿 이름(write_draft 등)")],
    file: Annotated[Path, typer.Option(help="새 템플릿 본문 파일")],
    note: Annotated[str, typer.Option()] = "",
) -> None:
    """최신 버전을 부모로 템플릿 하나만 바꾼 자식 버전을 만든다. 재배포 없이 다음 라운드부터 쓰인다."""

    async def _edit(service: PipelineVersionService, database, settings):
        _echo(meta_of(await service.edit_prompt(family, name, file.read_text(encoding="utf-8"), note=note)))
        return False

    _run_pipeline(_edit)


@pipeline.command("edit-param")
def pipeline_edit_param(
    assignments: Annotated[
        list[str], typer.Argument(help="key.path=value (값은 JSON. 예: publish.requires_approval=false)")
    ],
    note: Annotated[str, typer.Option()] = "",
) -> None:
    """조절 값만 바꾼 자식 버전을 만든다."""
    parsed: dict[str, Any] = {}
    for assignment in assignments:
        key, separator, raw = assignment.partition("=")
        if not separator:
            typer.echo(f"{assignment}: key=value 꼴이어야 한다", err=True)
            raise typer.Exit(1)
        # 값은 JSON 으로 먼저 읽는다(false·3·0.5·["a"]). JSON 이 아니면 문자열이다(structure.language=ko 처럼 따옴표 없이 쓰게)
        try:
            parsed[key] = json.loads(raw)
        except json.JSONDecodeError:
            parsed[key] = raw

    async def _edit(service: PipelineVersionService, database, settings):
        _echo(meta_of(await service.edit_parameters(parsed, note=note)))
        return False

    _run_pipeline(_edit)


@pipeline.command("edit-routes")
def pipeline_edit_routes(
    file: Annotated[Path, typer.Option(help="라우트 JSON 배열(agents_design/routes.example.json 형식)")],
    note: Annotated[str, typer.Option()] = "",
) -> None:
    """routes·SLOTS·LEVEL_CRITERIA 만 바꾼 자식 버전을 만든다. 옛 seed-routes 의 자리다."""

    async def _edit(service: PipelineVersionService, database, settings):
        documents = json.loads(file.read_text(encoding="utf-8"))
        _echo(meta_of(await service.edit_routes(documents, note=note)))
        return False

    _run_pipeline(_edit)


# ---------- 용어 사전·제작 기록 (설계 08 문서 2.5절, 3.4절) ----------

glossary = typer.Typer(help="용어 사전 승인·정리 (설계 agents_design/08 문서 2.5절)", no_args_is_help=True)
agents.add_typer(glossary, name="glossary")
provenance = typer.Typer(help="제작 기록 (설계 agents_design/08 문서 3절)", no_args_is_help=True)
agents.add_typer(provenance, name="provenance")


def _run_glossary(coroutine_factory):
    """GlossaryService 를 조립해 코루틴을 돌린다. 입력 오류·없는 키는 메시지와 종료 코드 1."""

    async def _main(database, settings):
        return await coroutine_factory(build_glossary_service(database, settings))

    return _run_agents(_main)


def _split_csv(value: str | None) -> list[str] | None:
    return [each.strip() for each in value.split(",") if each.strip()] if value else None


@glossary.command("pending")
def glossary_pending(
    draft_id: Annotated[str | None, typer.Option(help="그 글이 제안한 것만")] = None,
    post_id: Annotated[str | None, typer.Option(help="그 게시글이 제안한 것만")] = None,
) -> None:
    """PROPOSED 표제어·뜻·별칭과 보류 항목. 라운드가 끝난 뒤 사람이 읽는 목록이다."""

    async def _pending(service):
        _echo(await service.pending(draft_id=draft_id, post_id=post_id))

    _run_glossary(_pending)


@glossary.command("show")
def glossary_show(post_or_draft_id: str) -> None:
    """그 글의 항목(표시 여부 포함)·보류·버린 것."""

    async def _show(service):
        _echo(await service.show(post_or_draft_id))

    _run_glossary(_show)


@glossary.command("approve")
def glossary_approve(post_or_draft_id: str, approved_by: Annotated[str, typer.Option()] = "cli") -> None:
    """그 글이 제안한 표제어·뜻·별칭을 한 번에 승인한다. 구분 문구 불변식에 걸린 뜻은 남기고 사유를 찍는다."""

    async def _approve(service):
        _echo(await service.approve_all(post_or_draft_id, approved_by=approved_by))

    _run_glossary(_approve)


@glossary.command("approve-headword")
def glossary_approve_headword(
    key: str,
    approved_by: Annotated[str, typer.Option()] = "cli",
    term_ko: Annotated[str | None, typer.Option(help="표준 한국어 표기를 고쳐 승인")] = None,
    term_en: Annotated[str | None, typer.Option(help="영어 원어를 고쳐 승인")] = None,
) -> None:
    """표제어 승인."""

    async def _approve(service):
        headword = await service.approve_headword(key, approved_by=approved_by, term_ko=term_ko, term_en=term_en)
        _echo(headword.model_dump(mode="json"))

    _run_glossary(_approve)


@glossary.command("approve-sense")
def glossary_approve_sense(
    sense_key: str,
    approved_by: Annotated[str, typer.Option()] = "cli",
    definition: Annotated[
        str | None, typer.Option(help="정의를 고쳐 승인 (사전체 명사구: '…객체들의 묶음'. ~다 어미 금지)")
    ] = None,
    distinguisher: Annotated[str | None, typer.Option(help="다른 뜻과 가르는 한 줄")] = None,
    domains: Annotated[str | None, typer.Option(help="영역 슬러그, 쉼표로 (db,backend)")] = None,
    term_ko: Annotated[str | None, typer.Option(help="이 뜻만의 표기")] = None,
) -> None:
    """뜻 승인. 표제어가 PROPOSED 면 함께 승인한다. 승인된 뜻이 둘 이상이면 모두 구분 문구가 있어야 한다."""

    async def _approve(service):
        sense = await service.approve_sense(
            sense_key,
            approved_by=approved_by,
            definition=definition,
            distinguisher=distinguisher,
            domains=_split_csv(domains),
            term_ko=term_ko,
        )
        _echo(sense.model_dump(mode="json"))

    _run_glossary(_approve)


@glossary.command("reject-headword")
def glossary_reject_headword(key: str) -> None:
    """표제어 거절. 그 뜻을 가리키던 항목은 표시되지 않는다."""

    async def _reject(service):
        _echo((await service.reject_headword(key)).model_dump(mode="json"))

    _run_glossary(_reject)


@glossary.command("reject-sense")
def glossary_reject_sense(sense_key: str) -> None:
    """뜻 거절(철회 포함)."""

    async def _reject(service):
        _echo((await service.reject_sense(sense_key)).model_dump(mode="json"))

    _run_glossary(_reject)


@glossary.command("alias")
def glossary_alias(key: str, alias: str) -> None:
    """별칭 승인. proposed_aliases 에 있으면 거기서 옮긴다. 다른 표제어가 그 키를 가지면 거절한다."""

    async def _alias(service):
        _echo((await service.add_alias(key, alias)).model_dump(mode="json"))

    _run_glossary(_alias)


@glossary.command("unalias")
def glossary_unalias(key: str, alias: str) -> None:
    """별칭 삭제."""

    async def _unalias(service):
        _echo((await service.remove_alias(key, alias)).model_dump(mode="json"))

    _run_glossary(_unalias)


@glossary.command("resolve")
def glossary_resolve(
    post_id: str, surface: Annotated[str, typer.Option()], sense: Annotated[str, typer.Option()]
) -> None:
    """보류 항목에 뜻을 붙인다."""

    async def _resolve(service):
        _echo((await service.resolve_pending(post_id, surface=surface, sense_key=sense)).model_dump(mode="json"))

    _run_glossary(_resolve)


@glossary.command("drop")
def glossary_drop(post_id: str, surface: Annotated[str, typer.Option()]) -> None:
    """항목·보류를 지운다."""

    async def _drop(service):
        _echo((await service.drop(post_id, surface=surface)).model_dump(mode="json"))

    _run_glossary(_drop)


@glossary.command("extract")
def glossary_extract(
    draft_id: Annotated[str | None, typer.Argument(help="보충할 글")] = None,
    missing: Annotated[bool, typer.Option(help="PUBLISHED 인데 추출 기록이 없는 글 전부")] = False,
) -> None:
    """보충. 발행본으로 GLOSSARY 호출을 돌려 글 기록을 쓰고, PUBLISHED 면 post_glossary 를 다시 만든다(사람의 resolve·drop 은 사라진다)."""
    if draft_id is None and not missing:
        typer.echo("draft_id 나 --missing 중 하나가 필요하다.", err=True)
        raise typer.Exit(1)

    async def _extract(service):
        targets = [draft_id] if draft_id is not None else await service.list_missing_draft_ids()
        for target in targets:
            record = await service.backfill(target)
            _echo({"draft_id": target, "glossary": record.model_dump(mode="json")})

    _run_glossary(_extract)


@glossary.command("dedupe")
def glossary_dedupe(out: Annotated[Path | None, typer.Option(help="쌍 목록을 쓸 파일")] = None) -> None:
    """비슷한 표제어 쌍을 찍는다(코드 유사도). DB 는 바꾸지 않는다. 사람이 읽고 merge 로 적용한다."""

    async def _dedupe(service):
        pairs = await service.dedupe_candidates()
        if out is not None:
            out.write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
            typer.echo(f"{len(pairs)}쌍을 {out} 에 썼다.")
        else:
            _echo(pairs)

    _run_glossary(_dedupe)


@glossary.command("merge")
def glossary_merge(merge_key: str, into: Annotated[str, typer.Option()]) -> None:
    """표제어 병합: merge_key 의 키·별칭을 into 로 옮기고 뜻의 headword_key 를 바꾼 뒤 MERGED 로 둔다."""

    async def _merge(service):
        _echo((await service.merge_headwords(merge_key, into_key=into)).model_dump(mode="json"))

    _run_glossary(_merge)


@glossary.command("merge-sense")
def glossary_merge_sense(from_key: str, into: Annotated[str, typer.Option()]) -> None:
    """뜻 병합: 글 항목을 옮기고 글 목록을 합친 뒤 from 을 REJECTED 로 둔다."""

    async def _merge(service):
        _echo((await service.merge_senses(from_key, into_key=into)).model_dump(mode="json"))

    _run_glossary(_merge)


@glossary.command("check")
def glossary_check() -> None:
    """무결성 검사. 문제가 있으면 종료 코드 1."""

    async def _check(service):
        problems = await service.check()
        _echo(problems)
        if problems:
            raise typer.Exit(1)

    _run_glossary(_check)


@glossary.command("terms")
def glossary_terms(
    q: Annotated[str | None, typer.Option(help="표기·별칭 부분 일치")] = None,
    page: Annotated[int, typer.Option(min=1)] = 1,
    size: Annotated[int, typer.Option(min=1, max=200)] = 50,
) -> None:
    """승인된 사전을 찍는다 (읽기 API 와 같은 응답)."""

    async def _terms(database, settings):
        result = await build_glossary_view_service(database, settings).list_terms(q=q, page=page, size=size)
        _echo(result.model_dump(mode="json"))

    _run_agents(_terms)


@provenance.command("build")
def provenance_build(
    draft_id: Annotated[str | None, typer.Argument(help="PUBLISHED 글")] = None,
    missing: Annotated[bool, typer.Option(help="derived_at 이 없는 PUBLISHED 글 전부(정리와 같은 일)")] = False,
    rebuild: Annotated[bool, typer.Option(help="있는 기록을 다시 만들어 덮어쓴다")] = False,
) -> None:
    """제작 기록을 만든다. LLM 호출 없음."""
    if draft_id is None and not missing:
        typer.echo("draft_id 나 --missing 중 하나가 필요하다.", err=True)
        raise typer.Exit(1)

    async def _build(database, settings):
        if draft_id is not None:
            service = build_provenance_service(database, settings)
            record = await service.build_for_draft(draft_id, rebuild=rebuild, built_by="cli")
            _echo(record.model_dump(mode="json"))
        else:
            written = await build_derived_records_writer(database).write_missing(built_by="cli")
            _echo({"written": written})

    _run_agents(_build)


@provenance.command("show")
def provenance_show(post_id: str) -> None:
    """게시글 id 로 제작 기록을 찍는다 (공개 API 와 같은 응답)."""

    async def _show(database, settings):
        _echo((await build_provenance_service(database, settings).get_public(post_id)).model_dump(mode="json"))

    _run_agents(_show)


if __name__ == "__main__":
    cli()
