"""설명문 파이프라인 CLI. `python -m cli.expository seed` 로 프롬프트·라우트를 파이프라인 버전에 넣고, `run` 으로 글 하나를 만든다.

프롬프트·단계별 모델은 Mongo 파이프라인 버전에 있다(09 문서 D-99). 한 단계만 고칠 때는 기존 명령
`python -m cli agents pipeline edit-prompt expository <단계> --file …`·`edit-route EXPOSITORY:<단계> …` 를 쓴다.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.dependencies import build_expository_pipeline, build_pipeline_version_service, open_async_database
from app.services.expository import DEFAULT_MAX_WORDS, LAST_STAGE, RunSeed
from app.services.pipeline_versions import meta_of

EXPOSITORY_DIR = Path(__file__).resolve().parents[1] / "pipeline" / "expository"  # 버전에 넣는 입력(프롬프트·라우트)
RUNS_DIR = EXPOSITORY_DIR / "runs"

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
    run_dir: Annotated[
        Path | None, typer.Option("--run-dir", help="이어 할 실행 폴더. 없으면 runs/ 아래에 새로 만든다")
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
    max_words: Annotated[
        int, typer.Option("--max-words", help="5단계 영어 본문의 단어 상한(코드 제외)")
    ] = DEFAULT_MAX_WORDS,
) -> None:
    """1~9단계를 최신 파이프라인 버전으로 돌린다. 같은 --run-dir 로 다시 부르면 입력이 바뀌지 않은 단계는 건너뛴다."""
    target = run_dir or RUNS_DIR / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    topic = json.loads(topic_file.read_text(encoding="utf-8")) if topic_file else None
    seed_values = RunSeed(theme_keyword=theme, avoid_topics=avoid or [], topic=topic, max_words=max_words)
    typer.echo(f"실행 폴더: {target}")

    async def _pipeline(database, settings) -> dict[str, Any] | None:
        return await build_expository_pipeline(database, settings, target).run(seed_values, stop_after)

    final = _run(_pipeline)
    if final is None:
        typer.echo(f"{stop_after}단계까지 마쳤다. 이어 하려면 --run-dir {target}")
    else:
        typer.echo(f"완료: {target / 'final.md'} (파이프라인 버전 {final['pipeline_version']})")


if __name__ == "__main__":
    app()
