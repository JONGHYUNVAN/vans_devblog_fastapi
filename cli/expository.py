"""설명문 파이프라인 CLI. `python -m cli.expository run` 으로 글 하나를 끝까지 만든다.

기존 `python -m cli agents …` 명령과 따로 둔다. 이 파이프라인은 Mongo 없이 로컬 실행 폴더(pipeline/expository/runs/)에 결과를 남긴다.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from app.core.config import get_settings
from app.core.exceptions import LLMOutputError, LLMRetriesExhaustedError, LLMTransportError
from app.dependencies import build_expository_pipeline
from app.services.expository import DEFAULT_MAX_WORDS, RunSeed

RUNS_DIR = Path(__file__).resolve().parents[1] / "pipeline" / "expository" / "runs"
LAST_STAGE = 9

app = typer.Typer(help="설명문 파이프라인 (pipeline/expository)")


@app.callback()
def main() -> None:
    """설명문 파이프라인. 명령이 하나뿐이어도 `run` 을 적게 하려고 콜백을 둔다(없으면 Typer 가 하위 명령 이름을 받지 않는다)."""


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
    """1~9단계를 돌린다. 같은 --run-dir 로 다시 부르면 입력이 바뀌지 않은 단계는 건너뛴다."""
    target = run_dir or RUNS_DIR / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    topic = json.loads(topic_file.read_text(encoding="utf-8")) if topic_file else None
    pipeline = build_expository_pipeline(get_settings(), target)
    seed = RunSeed(theme_keyword=theme, avoid_topics=avoid or [], topic=topic, max_words=max_words)
    typer.echo(f"실행 폴더: {target}")
    try:
        final = asyncio.run(pipeline.run(seed, stop_after))
    except (LLMRetriesExhaustedError, LLMOutputError, LLMTransportError) as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(1) from exc
    if final is None:
        typer.echo(f"{stop_after}단계까지 마쳤다. 이어 하려면 --run-dir {target}")
    else:
        typer.echo(f"완료: {target / 'final.md'}")


if __name__ == "__main__":
    app()
