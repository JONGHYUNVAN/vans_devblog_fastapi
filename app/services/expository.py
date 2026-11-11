"""설명문 파이프라인 러너. 단계를 차례로 부르고 검사에 걸리면 오류를 붙여 다시 부르며, 단계 결과를 실행 폴더에 남긴다.

프롬프트·단계별 모델·조절 값은 실행을 시작할 때의 최신 파이프라인 버전(Mongo `agent_pipeline_versions`)에서 읽고, 호출은
AgentCallService 를 거쳐 영수증(`agent_calls`, kind EXPOSITORY, round_id 는 실행 id, item_id 는 단계 key)에 버전 id 와 함께
남는다(09 문서 D-99). 기존 라운드 러너(services/runner.py)와 따로 돌고 상태 기계·발행이 없다.
라운드와 달리 버전을 고정하지 않는다(D-73 과 다르다): 같은 실행 폴더로 다시 부르면 그때의 최신 버전을 쓴다. 프롬프트를
고친 단계부터 다시 돌리는 것이 이어 하기의 목적이고, 어느 버전으로 부른 호출인지는 영수증마다 남는다.
이어 하기: 단계 결과 파일이 있고 입력을 채운 프롬프트가 지난번과 같으면 그 단계를 다시 부르지 않는다. 앞 단계 결과나 버전의
프롬프트가 바뀌면 그 단계부터 다시 부른다. 1단계는 입력에 Post BE 카테고리가 들어가므로 결과 파일이 있으면 그대로 쓴다.
"""

import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from app.agents.expository import (
    RETRY_GUIDANCE,
    STAGES,
    SYSTEM_INSTRUCTION,
    Stage,
    parse_output,
    render_prompt,
    stage_models,
    stage_prompts,
)
from app.clients.publisher.post_be import PostBEPublisher
from app.core.exceptions import LLMOutputError
from app.models.agent_call import ChatMessage
from app.models.enums import CallKind
from app.models.model_binding import CallModel
from app.models.pipeline import Parameters
from app.repositories.expository_run import ExpositoryRunRepository
from app.services.agent_calls import AgentCallService
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
from app.services.pipeline_versions import PipelineVersionService
from app.services.round import category_options

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORDS = 3000  # 기존 draft_max_chars 20000자의 60~70%를 영어 단어로 어림한 값이다. 재 보지 않았다
LAST_STAGE = len(STAGES)
RUN_ID_PREFIX = "expository-"  # 영수증의 round_id. 라운드 id 와 섞이지 않게 앞에 붙인다
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

    인스턴스 하나가 실행 폴더 하나다. run 이 최신 버전을 읽어 그 실행 동안 쓴다.
    실패 계약: 검사를 끝내 통과하지 못하거나 벤더 호출이 끝내 실패하면 LLMRetriesExhaustedError(출력이 한도를 더 올릴 수 없이
    잘리면 그 하위 LLMOutputTruncatedError), 뼈대 사실 확인이 고칠 수 없는 오류를 찾거나 주어진 1단계 결과가 틀리면
    LLMOutputError, 버전이 없거나 최신 버전에 설명문 프롬프트·키가 있는 라우트 후보가 없으면 ValueError.
    그때까지의 결과는 실행 폴더에, 모든 시도는 영수증에 남는다.
    """

    def __init__(
        self,
        *,
        calls: AgentCallService,
        versions: PipelineVersionService,
        providers: Collection[str],
        repository: ExpositoryRunRepository,
        publisher: PostBEPublisher | None,
        main_categories: Collection[str],
    ) -> None:
        self._calls = calls
        self._versions = versions
        self._providers = providers
        self._repository = repository
        self._publisher = publisher
        self._main_categories = main_categories
        self._run_id = f"{RUN_ID_PREFIX}{repository.run_dir.name}"
        # run 이 최신 버전에서 채운다. 단계 메서드는 run 안에서만 불린다
        self._version_id = ""
        self._prompts: dict[str, dict[str, Any]] = {}
        self._models: dict[str, CallModel] = {}
        self._parameters = Parameters()

    async def run(self, seed: RunSeed, stop_after: int = LAST_STAGE) -> dict[str, Any] | None:
        """stop_after 단계까지 돌린다. 9단계까지 끝나면 최종 결과(final.json 내용)를 돌려주고, 그 전에 멈추면 None."""
        version = await self._versions.require_latest()
        self._version_id = version.version_id
        self._prompts = stage_prompts(version.prompts)
        self._models = stage_models(version.routes, self._providers)
        # 번역본의 길이·문체·코드 주석 기준과 재시도 수는 기존 파이프라인과 같은 조절 값이다
        self._parameters = version.parameters
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
            "pipeline_version": self._version_id,  # 마지막 실행의 버전. 단계별 버전은 영수증에 있다
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
        """검사를 통과한 출력. 걸리면 AgentCallService 가 직전 출력과 오류를 대화에 붙여 고치게 한다(처음부터 다시 쓰지 않게)."""
        await self._repository.put_text(f"{stage.key}.prompt.json", prompt)

        def parse(text: str) -> dict[str, Any]:
            try:
                output = parse_output(text)
            except ValueError as exc:
                raise LLMOutputError(str(exc)) from exc
            errors = check(output)
            if errors:
                raise LLMOutputError("\n".join(f"- {error}" for error in errors))
            return output

        # 스키마는 넘기지 않는다(call_text): 검색 단계는 구조화 출력과 같이 쓸 수 없고, 출력 검사는 parse 가 한다
        output, _ = await self._calls.call_text(
            kind=CallKind.EXPOSITORY,
            round_id=self._run_id,
            binding=self._models[stage.key],
            messages=[
                ChatMessage(role="system", content=SYSTEM_INSTRUCTION),
                ChatMessage(role="user", content=prompt),
            ],
            parse=parse,
            web_search=stage.web_search,
            item_id=stage.key,
            max_attempts=self._parameters.max_llm_retries,
            pipeline_version=self._version_id,
            retry_guidance=RETRY_GUIDANCE,
        )
        return output

    async def _categories(self) -> list[dict[str, Any]]:
        if self._publisher is None:
            logger.warning("POST_BE_URL 이 없어 카테고리 목록 없이 주제를 고른다")
            return []
        options = category_options(await self._publisher.list_categories(), self._main_categories)
        return [option.model_dump() for option in options]

    @property
    def _run_dir(self) -> str:
        return f"실행 폴더 {self._repository.run_dir}"
