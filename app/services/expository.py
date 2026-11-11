"""설명문 파이프라인 러너 (pipeline/expository). 단계를 차례로 부르고, 코드 검사에 걸리면 오류를 붙여 같은 단계를 다시 부르며,
단계마다 결과를 실행 폴더에 남긴다.

기존 라운드 러너(services/runner.py)와 따로 돈다. Mongo·상태 기계·발행 없이 로컬 CLI 에서 글 하나를 끝까지 만든다.
이어 하기: 단계 결과 파일이 있고 입력을 채운 프롬프트가 지난번과 같으면 그 단계를 다시 부르지 않는다. 앞 단계 결과나 프롬프트 파일이
바뀌면 그 단계부터 다시 부른다. 1단계는 입력에 Post BE 카테고리가 들어가므로 결과 파일이 있으면 그대로 쓴다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agents.expository import STAGES, SYSTEM_INSTRUCTION, Stage, StageRoute, parse_output, render_prompt
from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse
from app.clients.llm.registry import LLMClientRegistry
from app.clients.publisher.post_be import PostBEPublisher
from app.core.exceptions import LLMOutputError, LLMOutputTruncatedError, LLMRetriesExhaustedError, LLMTransportError
from app.models.pipeline import Parameters
from app.repositories.expository_run import ExpositoryRunRepository
from app.services.expository_assembly import apply_patches, assemble_article, first_paragraph
from app.services.expository_checks import (
    article_section_ids,
    blocking_claims,
    check_article,
    check_article_fact_check,
    check_outline,
    check_outline_fact_check,
    check_review,
    check_summary,
    check_summary_glossary,
    check_topic,
    check_translation,
)
from app.services.round import category_options

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORDS = 3000  # 기존 draft_max_chars 20000자의 60~70%를 영어 단어로 어림한 값이다. 재 보지 않았다
TRANSPORT_RETRIES = 2  # 시간 초과·5xx·429 는 두 번까지 다시 부른다(D-97 과 같은 횟수)
CHECK_RETRIES = 2  # 검사 실패는 오류를 붙여 두 번까지 고치게 한다(기존 max_validation_retries 와 같은 값)
TRANSPORT_BACKOFF_S = 5.0
TOPIC_KEYS = ("topic", "core_argument", "focus_mechanisms", "out_of_scope")
FINAL_JSON = "final.json"
FINAL_MARKDOWN = "final.md"

Check = Callable[[dict[str, Any]], list[str]]


@dataclass
class RunSeed:
    """실행 시작값. topic 을 주면 1단계를 부르지 않고 그 값을 1단계 결과로 쓴다."""

    theme_keyword: str = ""
    avoid_topics: list[str] = field(default_factory=list)
    topic: dict[str, Any] | None = None
    max_words: int = DEFAULT_MAX_WORDS


class ExpositoryPipeline:
    """설명문 글 하나를 1~9단계로 만든다.

    실패 계약: 검사를 끝내 통과하지 못하면 LLMRetriesExhaustedError, 출력이 한도에서 잘리면 LLMOutputTruncatedError,
    뼈대 사실 확인이 고칠 수 없는 오류를 찾거나 주어진 1단계 결과가 틀리면 LLMOutputError, 벤더 호출이 끝내 실패하면
    LLMTransportError, routes.json 후보 중 키가 있는 벤더가 없으면 ValueError. 그때까지의 결과는 실행 폴더에 남는다.
    """

    def __init__(
        self,
        *,
        registry: LLMClientRegistry,
        routes: dict[str, list[StageRoute]],
        prompts: dict[str, dict[str, Any]],
        repository: ExpositoryRunRepository,
        publisher: PostBEPublisher | None,
        main_categories: Collection[str],
        timeout_s: float,
        parameters: Parameters | None = None,
        transport_backoff_s: float = TRANSPORT_BACKOFF_S,
    ) -> None:
        self._registry = registry
        self._routes = routes
        self._prompts = prompts
        self._repository = repository
        self._publisher = publisher
        self._main_categories = main_categories
        self._timeout_s = timeout_s
        # 번역본의 길이·문체·코드 주석 기준. 기존 파이프라인의 코드 기본값을 쓴다(운영 버전 문서는 Mongo 에 있고 이 러너는 읽지 않는다)
        self._parameters = parameters if parameters is not None else Parameters()
        self._transport_backoff_s = transport_backoff_s

    async def run(self, seed: RunSeed, stop_after: int = len(STAGES)) -> dict[str, Any] | None:
        """stop_after 단계까지 돌린다. 9단계까지 끝나면 최종 결과(final.json 내용)를 돌려주고, 그 전에 멈추면 None."""
        steps: list[Callable[[Stage, dict[str, Any]], Awaitable[None]]] = [
            self._topic,
            self._outline,
            self._review,
            self._outline_fact_check,
            self._write,
            self._article_fact_check,
            self._summary_glossary,
            self._translate,
            self._summarize,
        ]
        state: dict[str, Any] = {"seed": seed}
        for stage, step in zip(STAGES, steps, strict=True):
            if stage.number > stop_after:
                return None
            await step(stage, state)
        return state["final"]

    async def _topic(self, stage: Stage, state: dict[str, Any]) -> None:
        seed: RunSeed = state["seed"]
        name = f"{stage.key}.json"
        topic = seed.topic if seed.topic is not None else await self._repository.get_json(name)
        if topic is not None:
            errors = check_topic(topic, [], given=True)
            if errors:
                raise LLMOutputError(f"{stage.key}: 주어진 1단계 결과가 검사에 걸렸다: {errors} ({self._run_dir})")
        else:
            categories = await self._categories()
            inputs = {"theme_keyword": seed.theme_keyword, "avoid_topics": seed.avoid_topics, "categories": categories}
            prompt = render_prompt(self._prompts[stage.key], inputs)
            topic = await self._call_until_valid(stage, prompt, lambda output: check_topic(output, categories))
        await self._repository.put_json(name, topic)
        state["topic"] = topic

    async def _outline(self, stage: Stage, state: dict[str, Any]) -> None:
        inputs = {key: state["topic"][key] for key in TOPIC_KEYS}
        state["outline"] = await self._run_stage(stage, inputs, lambda output: check_outline(output, inputs))

    async def _review(self, stage: Stage, state: dict[str, Any]) -> None:
        inputs = {**{key: state["topic"][key] for key in TOPIC_KEYS}, "outline": state["outline"]}
        output = await self._run_stage(stage, inputs, lambda output: check_review(output, inputs))
        state["reviewed"] = output["outline"]

    async def _outline_fact_check(self, stage: Stage, state: dict[str, Any]) -> None:
        inputs = {"topic": state["topic"]["topic"], "outline": state["reviewed"]}
        output = await self._run_stage(stage, inputs, lambda output: check_outline_fact_check(output, inputs))
        blocking = blocking_claims(output)
        if blocking:
            # 섹션의 메커니즘이나 논제 자체가 틀렸다. 문장만 고치면 앞뒤가 맞지 않는 글이 되므로 뼈대부터 다시 만든다
            raise LLMOutputError(
                f"{stage.key}: 메커니즘이나 논제를 바꿔야 고칠 수 있는 주장이 있다: {blocking}. "
                f"2_outline.json 을 지우고 다시 돌린다 ({self._run_dir})"
            )
        state["verified"] = output["outline"]

    async def _write(self, stage: Stage, state: dict[str, Any]) -> None:
        verified = state["verified"]
        inputs = {
            "outline": verified,
            "out_of_scope": state["topic"]["out_of_scope"],
            "max_words": state["seed"].max_words,
        }
        state["article"] = await self._run_stage(stage, inputs, lambda output: check_article(output, verified))

    async def _article_fact_check(self, stage: Stage, state: dict[str, Any]) -> None:
        verified, article = state["verified"], state["article"]
        inputs = {"outline": verified, "content_markdown": article["content_markdown"]}
        output = await self._run_stage(stage, inputs, check_article_fact_check)
        checked, rejected = apply_patches(
            article["content_markdown"],
            output["patches"],
            lambda markdown: check_article({"title": article["title"], "content_markdown": markdown}, verified),
        )
        await self._repository.put_text(f"{stage.key}.article.md", checked)
        await self._repository.put_json(f"{stage.key}.rejected_patches.json", rejected)
        state["checked"] = checked

    async def _summary_glossary(self, stage: Stage, state: dict[str, Any]) -> None:
        verified = state["verified"]
        # 결론에는 요약 표 대신 뼈대로 만든 전체 정리 표가 들어간다
        section_ids = article_section_ids(len(verified["deep_dive_sections"]))[:-1]
        inputs = {"content_markdown": state["checked"], "section_ids": section_ids}
        output = await self._run_stage(stage, inputs, lambda output: check_summary_glossary(output, section_ids))
        assembled = assemble_article(state["checked"], output["section_summaries"], verified, output["terms"])
        await self._repository.put_text(f"{stage.key}.article.md", assembled)
        state["assembled"], state["terms"] = assembled, output["terms"]

    async def _translate(self, stage: Stage, state: dict[str, Any]) -> None:
        assembled = state["assembled"]
        inputs = {"title": state["article"]["title"], "content_markdown": assembled, "terms": state["terms"]}
        state["translated"] = await self._run_stage(
            stage, inputs, lambda output: check_translation(output, assembled, self._parameters)
        )

    async def _summarize(self, stage: Stage, state: dict[str, Any]) -> None:
        translated, topic = state["translated"], state["topic"]
        inputs = {"title": translated["title"], "first_paragraph": first_paragraph(translated["content_markdown"])}
        output = await self._run_stage(stage, inputs, check_summary)
        final = {
            "title": translated["title"],
            "summary": output["summary"],
            "content_markdown": translated["content_markdown"],
            "keywords": topic.get("keywords", []),
            "main_category_id": topic.get("main_category_id"),
            "sub_category_id": topic.get("sub_category_id"),
            "topic": topic["topic"],
            "core_argument": topic["core_argument"],
            "english_title": state["article"]["title"],
        }
        await self._repository.put_json(FINAL_JSON, final)
        await self._repository.put_text(
            FINAL_MARKDOWN, f"# {final['title']}\n\n> {final['summary']}\n\n{final['content_markdown']}"
        )
        state["final"] = final

    async def _run_stage(self, stage: Stage, inputs: dict[str, Any], check: Check) -> dict[str, Any]:
        """결과 파일이 있고 프롬프트가 지난번과 같으면 그 결과를 쓰고, 아니면 부른다."""
        prompt = render_prompt(self._prompts[stage.key], inputs)
        cached = await self._repository.get_json(f"{stage.key}.json")
        if cached is not None and await self._repository.get_text(f"{stage.key}.prompt.json") == prompt:
            return cached
        output = await self._call_until_valid(stage, prompt, check)
        await self._repository.put_json(f"{stage.key}.json", output)
        return output

    async def _call_until_valid(self, stage: Stage, prompt: str, check: Check) -> dict[str, Any]:
        """검사를 통과할 때까지 부른다. 실패하면 직전 출력과 오류를 대화에 붙여 고치게 한다(처음부터 다시 쓰지 않게)."""
        await self._repository.put_text(f"{stage.key}.prompt.json", prompt)
        route = self._route(stage)
        messages = [LLMMessage(role="system", content=SYSTEM_INSTRUCTION), LLMMessage(role="user", content=prompt)]
        errors: list[str] = []
        for attempt in range(1, CHECK_RETRIES + 2):
            response = await self._complete(stage, route, messages)
            await self._repository.put_text(f"{stage.key}.attempt{attempt}.txt", response.text)
            if response.truncated:
                await self._log(stage, route, attempt, response, ["truncated"])
                raise LLMOutputTruncatedError(
                    f"{stage.key}: 출력이 {route.max_output_tokens} 토큰 한도에서 잘렸다. "
                    f"routes.json 의 max_output_tokens 를 올린다 ({self._run_dir})"
                )
            try:
                output = parse_output(response.text)
                errors = check(output)
            except ValueError as exc:
                output, errors = {}, [str(exc)]
            await self._log(stage, route, attempt, response, errors)
            if not errors:
                return output
            messages = [
                *messages,
                LLMMessage(role="assistant", content=response.text),
                LLMMessage(role="user", content=_feedback(errors)),
            ]
        raise LLMRetriesExhaustedError(
            f"{stage.key}: {CHECK_RETRIES + 1}번 모두 검사에 걸렸다. 마지막 오류: {errors} ({self._run_dir})"
        )

    async def _complete(self, stage: Stage, route: StageRoute, messages: list[LLMMessage]) -> LLMResponse:
        """벤더 호출 하나. 전송 실패는 TRANSPORT_RETRIES 번까지 다시 부르고, 그래도 실패하면 LLMTransportError 를 그대로 던진다."""
        client = self._registry.get(route.provider)
        params = LLMParams(
            model_id=route.model_id,
            max_output_tokens=route.max_output_tokens,
            effort=route.effort,
            web_search=stage.web_search,
        )
        failures = 0
        while True:
            try:
                async with self._registry.gate(route.provider).slot():
                    # 스키마는 넘기지 않는다: 검색 단계는 구조화 출력과 같이 쓸 수 없고, 출력 검사는 parse_output 과 check 가 한다
                    return await client.complete(messages, None, params, self._timeout_s)
            except LLMTransportError as exc:
                failures += 1
                if failures > TRANSPORT_RETRIES:
                    raise
                logger.warning("%s 호출 실패, 다시 부른다(%d/%d): %s", stage.key, failures, TRANSPORT_RETRIES, exc)
                await asyncio.sleep(self._transport_backoff_s * failures)

    def _route(self, stage: Stage) -> StageRoute:
        candidates = self._routes[stage.key]
        for candidate in candidates:
            if candidate.provider in self._registry.providers:
                return candidate
        raise ValueError(
            f"{stage.key}: routes.json 후보 {[candidate.provider for candidate in candidates]} 중 API 키가 있는 벤더가 없다"
        )

    async def _categories(self) -> list[dict[str, Any]]:
        if self._publisher is None:
            logger.warning("POST_BE_URL 이 없어 카테고리 목록 없이 주제를 고른다")
            return []
        options = category_options(await self._publisher.list_categories(), self._main_categories)
        return [option.model_dump() for option in options]

    async def _log(
        self, stage: Stage, route: StageRoute, attempt: int, response: LLMResponse, errors: list[str]
    ) -> None:
        await self._repository.add_log(
            {
                "stage": stage.key,
                "attempt": attempt,
                "provider": route.provider,
                "model_id": route.model_id,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "stop_reason": response.stop_reason,
                "errors": errors,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    @property
    def _run_dir(self) -> str:
        return f"실행 폴더 {self._repository.run_dir}"


def _feedback(errors: list[str]) -> str:
    lines = "\n".join(f"- {error}" for error in errors)
    return (
        f"Your previous output failed these checks:\n{lines}\n"
        "Return the complete corrected JSON object, not only the changed parts, "
        "and keep following every instruction in the first message."
    )
