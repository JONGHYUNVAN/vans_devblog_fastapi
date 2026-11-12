"""설명문 파이프라인 CLI. `seed` 로 프롬프트·라우트를 파이프라인 버전에 넣고, `run` 으로 글 하나를 만들고, `show` 로 보고, `publish` 로 블로그에 올린다.

프롬프트·단계별 모델은 Mongo 파이프라인 버전에 있다(09 문서 D-99). 한 단계만 고칠 때는 기존 명령
`python -m cli agents pipeline edit-prompt expository <단계> --file …` 을 쓴다. 실행 결과는 Mongo `expository_runs` 다(D-100).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.dependencies import build_expository_pipeline, build_pipeline_version_service, open_async_database
from app.schemas.expository import ExpositoryResult
from app.services.expository import DEFAULT_MAX_WORDS, LAST_STAGE, RunSeed, new_run_id
from app.services.pipeline_versions import meta_of

EXPOSITORY_DIR = Path(__file__).resolve().parents[1] / "pipeline" / "expository"  # 버전에 넣는 입력(프롬프트·라우트)

app = typer.Typer(help="설명문 파이프라인 (pipeline/expository)", no_args_is_help=True)


def _run[T](coroutine_factory: Callable[..., Awaitable[T]]) -> T:
    """비동기 Mongo 연결을 열고 coroutine_factory(database, settings) 를 돌린다. 실패(AppError, 잘못된 값·없는 버전 ValueError, 없는 파일)는 메시지와 종료 코드 1."""
    settings = get_settings()
    if not settings.mongodb_db_name:
        typer.echo("MongoDB DB 이름(MONGODB_DATABASE 또는 URI 경로)이 없다.", err=True)
        raise typer.Exit(1)

    async def _main() -> T:
        async with open_async_database(settings) as database:
            return await coroutine_factory(database, settings)

    try:
        return asyncio.run(_main())
    except AppError as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(1) from exc
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def _write_markdown(result: ExpositoryResult, out: Path | None) -> None:
    """끝난 글을 파일로 쓰거나(--out) 표준 출력에 찍는다. 끝나지 않은 실행은 끝난 단계만 알린다."""
    if result.markdown is None:
        typer.echo(f"{result.run_id}: 끝난 단계 {result.finished_stages}. 이어 하려면 run --run-id {result.run_id}")
    elif out is None:
        typer.echo(result.markdown)
    else:
        out.write_text(result.markdown, encoding="utf-8", newline="\n")
        typer.echo(f"{result.run_id}: {out} (파이프라인 버전 {result.pipeline_version})")


@app.command()
def seed(
    directory: Annotated[
        Path, typer.Option("--dir", help="prompts/{단계}.json 과 routes.json 이 있는 디렉터리")
    ] = EXPOSITORY_DIR,
    note: Annotated[str, typer.Option()] = "",
) -> None:
    """디렉터리의 프롬프트·라우트를 최신 파이프라인 버전에 넣어 새 버전을 만든다. 최신과 같은 내용이면 거절한다."""

    async def _seed(database, settings) -> dict[str, Any]:
        version = await build_pipeline_version_service(database).import_expository(directory, note=note)
        return meta_of(version)

    typer.echo(json.dumps(_run(_seed), ensure_ascii=False, indent=2))


@app.command()
def run(
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="이어 할 실행 id. 없으면 새 실행(expository-<UTC 시각>)")
    ] = None,
    theme: Annotated[str, typer.Option("--theme", help="주제 키워드(1단계 입력)")] = "",
    avoid: Annotated[list[str] | None, typer.Option("--avoid", help="겹치면 안 되는 주제. 여러 번 줄 수 있다")] = None,
    topic_file: Annotated[
        Path | None,
        typer.Option(
            "--topic-file",
            help="1단계 결과 JSON(topic·core_argument·focus_mechanisms·out_of_scope). 주면 1단계를 부르지 않는다",
        ),
    ] = None,
    stop_after: Annotated[
        int, typer.Option("--stop-after", min=1, max=LAST_STAGE, help="이 단계까지만 돌린다")
    ] = LAST_STAGE,
    redo_from: Annotated[
        int | None,
        typer.Option("--redo-from", min=1, max=LAST_STAGE, help="이 단계부터의 결과를 지우고 다시 부른다"),
    ] = None,
    max_words: Annotated[
        int, typer.Option("--max-words", help="5단계 영어 본문의 단어 상한(코드 제외)")
    ] = DEFAULT_MAX_WORDS,
    out: Annotated[Path | None, typer.Option("--out", help="끝난 글(마크다운)을 쓸 파일. 없으면 찍지 않는다")] = None,
) -> None:
    """1~9단계를 최신 파이프라인 버전으로 돌린다. 같은 --run-id 로 다시 부르면 입력이 바뀌지 않은 단계는 건너뛴다."""
    target = run_id or new_run_id()
    topic = json.loads(topic_file.read_text(encoding="utf-8")) if topic_file else None
    seed_values = RunSeed(theme_keyword=theme, avoid_topics=avoid or [], topic=topic, max_words=max_words)
    typer.echo(f"실행 id: {target}")

    async def _pipeline(database, settings) -> ExpositoryResult:
        pipeline = build_expository_pipeline(database, settings)
        return await pipeline.run(target, seed_values, stop_after=stop_after, redo_from=redo_from)

    result = _run(_pipeline)
    if result.markdown is None or out is not None:
        _write_markdown(result, out)
    else:
        typer.echo(f"완료: {result.run_id} (파이프라인 버전 {result.pipeline_version}). 글은 show {result.run_id}")


@app.command()
def show(
    run_id: Annotated[str, typer.Argument(help="실행 id")],
    out: Annotated[Path | None, typer.Option("--out", help="글을 쓸 파일. 없으면 표준 출력")] = None,
) -> None:
    """실행의 끝난 글(제목·요약·본문 마크다운)을 찍는다. 끝나지 않았으면 끝난 단계만 알린다."""

    async def _show(database, settings) -> ExpositoryResult:
        return await build_expository_pipeline(database, settings).get_result(run_id)

    _write_markdown(_run(_show), out)


@app.command()
def publish(
    run_id: Annotated[str, typer.Argument(help="실행 id")],
    main_category_id: Annotated[
        str | None, typer.Option("--main-category-id", help="Post BE 메인 카테고리 id. 없으면 1단계 결과")
    ] = None,
    sub_category_id: Annotated[
        str | None, typer.Option("--sub-category-id", help="Post BE 서브 카테고리 id. 없으면 1단계 결과")
    ] = None,
    keyword: Annotated[
        list[str] | None, typer.Option("--keyword", help="태그. 여러 번 줄 수 있다. 없으면 1단계 결과")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="지난 발행의 결과를 모를 때, 블로그에 글이 없는 것을 확인하고 다시 올린다")
    ] = False,
) -> None:
    """끝난 글을 블로그에 올린다. 한 실행은 한 번만 올린다. 카테고리가 없거나 틀리면 고를 수 있는 쌍을 알려 준다."""

    async def _publish(database, settings) -> ExpositoryResult:
        return await build_expository_pipeline(database, settings).publish(
            run_id,
            main_category_id=main_category_id,
            sub_category_id=sub_category_id,
            keywords=keyword,
            force=force,
        )

    result = _run(_publish)
    typer.echo(f"{result.run_id}: 게시글 {result.external_post_id} 로 올렸다")


if __name__ == "__main__":
    app()
