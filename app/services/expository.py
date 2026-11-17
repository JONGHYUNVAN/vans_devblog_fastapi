"""설명문 파이프라인 러너. 단계를 차례로 부르고 검사에 걸리면 오류를 붙여 다시 부르며, 단계 결과를 실행 문서에 남긴다.

프롬프트·단계별 모델·조절 값은 실행을 시작할 때의 최신 파이프라인 버전(Mongo `agent_pipeline_versions`)에서 읽고, 호출은
AgentCallService 를 거쳐 영수증(`agent_calls`, kind EXPOSITORY, round_id 는 실행 id, item_id 는 단계 key)에 버전 id 와 함께
남는다(09 문서 D-99). 단계 결과와 완성된 글은 `expository_runs` 문서다(D-100). 발행은 run 과 따로 publish 가 한다(D-101).
기존 라운드 러너(services/runner.py)와 따로 돌고 상태 기계·발행이 없다.
라운드와 달리 버전을 고정하지 않는다(D-73 과 다르다): 같은 실행 id 로 다시 부르면 그때의 최신 버전을 쓴다. 프롬프트를
고친 단계부터 다시 돌리는 것이 이어 하기의 목적이고, 어느 버전으로 부른 호출인지는 영수증마다 남는다.
이어 하기: 단계 결과가 있고 입력을 채운 프롬프트가 그 결과를 만든 프롬프트와 같으면 그 단계를 다시 부르지 않는다. 앞 단계
결과나 버전의 프롬프트가 바뀌면 그 단계부터 다시 부른다. 1단계는 입력에 Post BE 카테고리가 들어가므로 결과가 있으면 그대로 쓴다.
뼈대의 양식은 글의 목적에 따라 다르다(D-104): 1단계가 버전 문서의 글 양식(`prompts.expository_form`) 중 하나를 고르고,
뒤 단계는 그 양식의 역할 목록으로 뼈대를 만들고 검사하며 섹션 순서를 정한다. 양식마다 다른 코드는 없다.
보내는 메시지의 배치는 조절 값 `expository.layouts` 가 호출 모델의 가족별로 고른다(D-103). 실행 문서에 남는 프롬프트는
배치와 무관한 중심 양식이라, 배치나 모델만 바꾼 뒤 이어 하면 끝난 단계를 다시 부르지 않는다.
"""

import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from string import Template
from typing import Any

from app.agents.expository import (
    DEFAULT_LAYOUT_KEY,
    DOCUMENT_RETRY_GUIDANCE,
    RETRY_GUIDANCE,
    STAGES,
    Stage,
    parse_document,
    parse_output,
    render_messages,
    render_prompt,
    stage_models,
    stage_prompts,
)
from app.agents.expository_forms import (
    form_choices,
    outline_view,
    repeatable_roles,
    review_view,
    role_specs,
    section_ids,
    stage_forms,
    write_view,
)
from app.agents.prompts.registry import prompt_family
from app.clients.publisher.base import PostCreatePayload
from app.clients.publisher.post_be import PostBEPublisher
from app.core.exceptions import InvalidRequestError, LLMOutputError, NotFoundError, PublishFailedError
from app.models.agent_call import ChatMessage
from app.models.enums import CallKind
from app.models.expository import ExpositoryArticle, ExpositoryPublication, ExpositoryRun
from app.models.model_binding import CallModel
from app.models.pipeline import Parameters, PublishNoteParameters
from app.models.tiptap import markdown_to_tiptap
from app.repositories.expository_run import ExpositoryRunRepository
from app.schemas.expository import ExpositoryResult
from app.services.agent_calls import AgentCallService
from app.services.expository_assembly import apply_patches, assemble_article, first_paragraph
from app.services.expository_checks import (
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

# 5단계 영어 본문(코드 제외)의 단어 상한. 첫 실행(2026-11-11)에서 영어 2,794단어 + 표·용어 정리 1,014단어가 한국어 22,945자
# (코드 7,900자 포함, 영어 1단어 ≈ 한국어 3.9자)가 되어 작성 상한 20,000자를 넘었다. 표를 줄인 것과 합쳐 19,000자 안팎을 겨눈다
DEFAULT_MAX_WORDS = 2200
LAST_STAGE = len(STAGES)
RUN_ID_PREFIX = "expository-"  # 실행 id 이자 영수증의 round_id. 라운드 id 와 섞이지 않게 앞에 붙인다
TOPIC_KEYS = ("topic", "core_argument", "focus_points", "out_of_scope")  # 1단계 결과 중 뼈대 단계에 들어가는 것
WRITE_STAGE = "5_write"
FACT_CHECK_STAGES = ("4_outline_fact_check", "6_article_fact_check")
_FACT_CHECK_LENS = "FACT_STRUCTURE"  # 글 끝 표기의 렌즈 라벨(사실·구조). 기존 라운드의 사실 확인 렌즈와 같은 이름이다
_LANGUAGE = "ko"
_TOPIC_MAX_CHARS = 200  # Post BE CreatePostDto.topic 상한

Check = Callable[[dict[str, Any]], list[str]]


def new_run_id(now: datetime | None = None) -> str:
    """새 실행 id `expository-{UTC 시각}`. 초 단위라 같은 초에 두 번 시작하면 같은 실행을 이어 하게 된다."""
    return f"{RUN_ID_PREFIX}{(now or datetime.now(UTC)).strftime('%Y%m%d-%H%M%S')}"


@dataclass
class RunSeed:
    """실행 시작값. topic 을 주면 1단계를 부르지 않고 그 값을 1단계 결과로 쓴다.

    form 은 글 양식 id 다(D-104). 주면 1단계가 그 양식으로만 주제를 고르고, topic 을 같이 주면 그 topic 의 양식이 된다.
    """

    theme_keyword: str = ""
    avoid_topics: list[str] = field(default_factory=list)
    topic: dict[str, Any] | None = None
    max_words: int = DEFAULT_MAX_WORDS
    form: str | None = None


class ExpositoryPipeline:
    """설명문 글 하나를 1~9단계로 만든다.

    run 이 최신 버전과 실행 문서를 읽어 그 호출 동안 쓴다. 같은 실행을 두 러너가 동시에 돌리지 않는다(문서를 통째로 덮어쓴다).
    실패 계약: 검사를 끝내 통과하지 못하거나 벤더 호출이 끝내 실패하면 LLMRetriesExhaustedError(출력이 한도를 더 올릴 수 없이
    잘리면 그 하위 LLMOutputTruncatedError), 뼈대 사실 확인이 고칠 수 없는 오류를 찾거나 주어진 1단계 결과가 틀리면
    LLMOutputError, 버전이 없거나 최신 버전에 설명문 프롬프트·글 양식·키가 있는 라우트 후보가 없거나 모르는 양식 id 를 주면 ValueError.
    그때까지 끝난 단계의 결과는 실행 문서에, 모든 시도는 영수증에 남는다.
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
        # run 이 최신 버전과 실행 문서에서 채운다. 단계 메서드는 run 안에서만 불린다
        self._version_id = ""
        self._prompts: dict[str, dict[str, Any]] = {}
        self._models: dict[str, CallModel] = {}
        self._parameters = Parameters()
        self._layouts: dict[str, str] = {}
        self._forms: dict[str, dict[str, Any]] = {}
        self._run = ExpositoryRun(run_id="")
        self._seed = RunSeed()

    async def run(
        self, run_id: str, seed: RunSeed, *, stop_after: int = LAST_STAGE, redo_from: int | None = None
    ) -> ExpositoryResult:
        """run_id 의 실행을 stop_after 단계까지 돌리고 그 상태를 돌려준다. 없는 id 면 새 실행이다.

        redo_from 을 주면 그 단계부터의 결과를 지우고 다시 부른다(사실 확인이 뼈대를 막았을 때 2, 글만 다시 쓸 때 5).
        """
        version = await self._versions.require_latest()
        self._version_id = version.version_id
        self._prompts = stage_prompts(version.prompts)
        # 양식이 없는 버전(D-104 전에 넣은 프롬프트)은 여기서 멈춘다. 호출을 시작한 뒤에 입력 키가 맞지 않아 멈추면 토큰만 쓴다
        self._forms = stage_forms(version.prompts)
        if seed.form is not None and seed.form not in self._forms:
            raise ValueError(f"모르는 글 양식 {seed.form!r}. 최신 버전의 양식: {sorted(self._forms)}")
        self._models = stage_models(version.routes, self._providers)
        # 번역본의 길이·문체·코드 주석 기준과 재시도 수는 기존 파이프라인과 같은 조절 값이다
        self._parameters = version.parameters
        self._layouts = version.parameters.expository.layouts
        self._run = await self._repository.get(run_id) or ExpositoryRun(run_id=run_id)
        self._seed = seed
        if redo_from is not None:
            self._run.drop_from([stage.key for stage in STAGES if stage.number >= redo_from])
        steps: list[Callable[[Stage], Awaitable[None]]] = [
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
        for stage, step in zip(STAGES, steps, strict=True):
            if stage.number > stop_after:
                break
            await step(stage)
        return to_result(self._run)

    async def get_result(self, run_id: str) -> ExpositoryResult:
        """실행 하나의 지금 상태. 없으면 NotFoundError."""
        run = await self._repository.get(run_id)
        if run is None:
            raise NotFoundError(f"expository run {run_id} does not exist")
        return to_result(run)

    async def publish(
        self,
        run_id: str,
        *,
        main_category_id: str | None = None,
        sub_category_id: str | None = None,
        keywords: list[str] | None = None,
        force: bool = False,
    ) -> ExpositoryResult:
        """끝난 글을 작성 모델 벤더의 계정으로 블로그(Post BE)에 올린다 (D-101). 한 실행은 한 번만 올린다.

        카테고리·키워드는 1단계 결과를 쓰고, 인자가 있으면 그 값이 이긴다(1단계를 사람이 준 실행은 카테고리가 없다).
        실패 계약: 없는 실행은 NotFoundError. 글이 끝나지 않았거나, 이미 올렸거나, 카테고리 쌍이 Post BE 에 없거나,
        지난 발행의 결과를 모르는데 force 가 아니면 InvalidRequestError. Post BE 설정·발행 계정이 없거나 본문을 변환할 수
        없으면 ValueError. 호출이 확정 실패하면 PublishFailedError(다시 올릴 수 있다), 결과를 모르면 PublishUncertainError
        (시도 시각이 남아 다음 호출이 막힌다. 블로그를 확인한 뒤 force 로 다시 올린다).
        """
        run = await self._repository.get(run_id)
        if run is None:
            raise NotFoundError(f"expository run {run_id} does not exist")
        article = run.article
        if article is None:
            raise InvalidRequestError(f"{run_id}: 9단계까지 끝나지 않아 올릴 글이 없다")
        if run.publication is not None:
            raise InvalidRequestError(f"{run_id}: 이미 게시글 {run.publication.external_post_id} 로 올렸다")
        if run.publish_attempted_at is not None and not force:
            raise InvalidRequestError(
                f"{run_id}: {run.publish_attempted_at.isoformat()} 의 발행 결과를 모른다. "
                "블로그에 글이 없는지 확인한 뒤 --force 로 다시 올린다"
            )
        if self._publisher is None:
            raise ValueError("Post BE publishing is not configured (POST_BE_URL)")
        version = await self._versions.require_latest()
        # D-101 전에 끝난 단계는 모델 기록이 없다. 없는 단계는 지금 버전의 라우트로 대신한다
        models = {**stage_models(version.routes, self._providers), **run.stage_models}
        writer = models[WRITE_STAGE]
        if not self._publisher.has_account(writer.provider):
            raise ValueError(
                f"publishing account for provider {writer.provider!r} is not configured (USER_SERVICE_URL too)"
            )
        main_id = main_category_id or article.main_category_id
        sub_id = sub_category_id or article.sub_category_id
        options = category_options(await self._publisher.list_categories(), self._main_categories)
        if (main_id, sub_id) not in {(option.main_category_id, option.sub_category_id) for option in options}:
            choices = "\n".join(
                f"  {option.main_label} / {option.sub_label}: "
                f"--main-category-id {option.main_category_id} --sub-category-id {option.sub_category_id}"
                for option in options
            )
            raise InvalidRequestError(
                f"{run_id}: 카테고리 쌍 ({main_id}, {sub_id}) 이 Post BE 에 없다. 고를 수 있는 쌍:\n{choices}"
            )
        note = build_publish_note(
            writer.model_id,
            [models[key].model_id for key in FACT_CHECK_STAGES if key in models],
            version.parameters.publish.note,
        )
        article.main_category_id, article.sub_category_id = main_id, sub_id
        if keywords:
            article.keywords = keywords
        payload = PostCreatePayload(
            title=article.title,
            content=markdown_to_tiptap(f"{article.content_markdown.rstrip()}\n\n{note}"),
            description=article.summary,
            tags=article.keywords,
            topic=article.topic[:_TOPIC_MAX_CHARS],
            main_category_id=main_id,
            sub_category_id=sub_id,
            language=_LANGUAGE,
        )
        # 호출 전에 시도를 적는다. 응답 전에 끊기면 이 값만 남아 다음 호출이 중복 게시를 만들지 않는다
        run.publish_attempted_at = datetime.now(UTC)
        await self._repository.upsert(run)
        try:
            result = await self._publisher.publish(payload, writer.provider)
        except PublishFailedError:
            run.publish_attempted_at = None  # 글이 만들어지지 않은 것이 확실하다. 다시 올릴 수 있게 지운다
            await self._repository.upsert(run)
            raise
        run.publication = ExpositoryPublication(
            external_post_id=result.external_post_id,
            published_at=datetime.now(UTC),
            provider=writer.provider,
            note=note,
        )
        await self._repository.upsert(run)
        return to_result(run)

    async def _topic(self, stage: Stage) -> None:
        form = self._seed.form
        topic = self._run.stage_outputs.get(stage.key)
        if self._seed.topic is not None:
            # 사람이 준 주제에 --form 을 같이 주면 그 양식으로 쓴다. 파일에 form 이 있어도 명령 인자가 이긴다
            topic = self._seed.topic if form is None else {**self._seed.topic, "form": form}
        elif topic is not None and form is not None and topic.get("form") != form:
            raise ValueError(
                f"{self._run.run_id} 의 1단계 결과는 양식 {topic.get('form')!r} 다. 양식 {form!r} 로 쓰려면 "
                "새 실행을 시작하거나 --redo-from 1 로 주제부터 다시 고른다"
            )
        if topic is not None:
            errors = check_topic(topic, [], self._forms, given=True)
            if errors:
                raise LLMOutputError(f"{stage.key}: 주어진 1단계 결과가 검사에 걸렸다: {errors} ({self._run.run_id})")
            await self._save_stage(stage, topic, prompt=None)
            return
        categories = await self._categories()
        forms = self._forms if form is None else {form: self._forms[form]}
        inputs = {
            "theme_keyword": self._seed.theme_keyword,
            "avoid_topics": self._seed.avoid_topics,
            "categories": categories,
            "forms": form_choices(forms),
        }
        prompt = render_prompt(self._prompts[stage.key], inputs)
        topic = await self._call_until_valid(stage, inputs, lambda output: check_topic(output, categories, forms))
        await self._save_stage(stage, topic, prompt)

    async def _outline(self, stage: Stage) -> None:
        topic, (form_id, form) = self._output("1_topic_thesis"), self._form()
        topic_inputs = {key: topic[key] for key in TOPIC_KEYS}
        inputs = {**topic_inputs, "form": outline_view(form_id, form)}
        await self._run_stage(stage, inputs, lambda output: check_outline(output, topic_inputs, form_id, form))

    async def _review(self, stage: Stage) -> None:
        topic, outline, (form_id, form) = self._output("1_topic_thesis"), self._output("2_outline"), self._form()
        if not repeatable_roles(form):
            # 여러 번 올 수 있는 본론 역할이 없는 양식은 뺄 region 도 채울 region 도 없다. 부르지 않고 뼈대를 그대로 넘긴다
            await self._save_stage(stage, {"outline": outline, "review_log": []}, prompt=None)
            return
        inputs = {
            **{key: topic[key] for key in TOPIC_KEYS},
            "outline": outline,
            "form": review_view(form_id, form),
        }
        await self._run_stage(stage, inputs, lambda output: check_review(output, inputs, form_id, form))

    async def _outline_fact_check(self, stage: Stage) -> None:
        form_id, form = self._form()
        inputs = {
            "topic": self._output("1_topic_thesis")["topic"],
            "outline": self._output("3_outline_review")["outline"],
        }
        output = await self._run_stage(
            stage, inputs, lambda output: check_outline_fact_check(output, inputs, form_id, form)
        )
        blocking = blocking_claims(output)
        if blocking:
            # 섹션의 메커니즘이나 논제 자체가 틀렸다. 문장만 고치면 앞뒤가 맞지 않는 글이 되므로 뼈대부터 다시 만든다
            raise LLMOutputError(
                f"{stage.key}: 메커니즘이나 논제를 바꿔야 고칠 수 있는 주장이 있다: {blocking}. "
                f"--redo-from 2 로 뼈대부터 다시 돌린다 ({self._run.run_id})"
            )

    async def _write(self, stage: Stage) -> None:
        verified, (form_id, form), sections = self._verified_outline(), self._form(), self._sections()
        inputs = {
            "outline": verified,
            "out_of_scope": self._output("1_topic_thesis")["out_of_scope"],
            "max_words": self._seed.max_words,
            "form": write_view(form_id, form, verified["regions"]),
        }
        await self._run_stage(stage, inputs, lambda output: check_article(output, sections))

    async def _article_fact_check(self, stage: Stage) -> None:
        verified, article, sections = self._verified_outline(), self._output("5_write"), self._sections()
        inputs = {"outline": verified, "content_markdown": article["content_markdown"]}
        output = await self._run_stage(stage, inputs, check_article_fact_check)
        checked, rejected = apply_patches(
            article["content_markdown"],
            output["patches"],
            lambda markdown: check_article({"title": article["title"], "content_markdown": markdown}, sections),
        )
        self._run.checked_markdown, self._run.rejected_patches = checked, rejected
        await self._save()

    async def _summary_glossary(self, stage: Stage) -> None:
        verified, checked = self._verified_outline(), self._run.checked_markdown or ""
        form, sections = self._form()[1], self._sections()
        # 마지막 섹션에는 요약 표 대신 뼈대로 만든 전체 정리 표가 들어간다
        summarized = sections[:-1]
        inputs = {"content_markdown": checked, "section_ids": summarized}
        output = await self._run_stage(stage, inputs, lambda output: check_summary_glossary(output, summarized))
        specs = role_specs(form)
        self._run.assembled_markdown = assemble_article(
            checked,
            output["section_summaries"],
            output["terms"],
            overview_header=(form["overview_header"][0], form["overview_header"][1]),
            overview_rows=[
                (region["heading"], region["takeaway"])
                for region in verified["regions"]
                if specs[region["role"]]["focus"]
            ],
            last_section=sections[-1],
        )
        await self._save()

    async def _translate(self, stage: Stage) -> None:
        assembled = self._run.assembled_markdown or ""
        inputs = {
            "title": self._output("5_write")["title"],
            "content_markdown": assembled,
            "terms": self._output("7_summary_glossary")["terms"],
        }
        await self._run_stage(stage, inputs, lambda output: check_translation(output, assembled, self._parameters))

    async def _summarize(self, stage: Stage) -> None:
        translated, topic = self._output("8_translate"), self._output("1_topic_thesis")
        inputs = {"title": translated["title"], "first_paragraph": first_paragraph(translated["content_markdown"])}
        output = await self._run_stage(stage, inputs, check_summary)
        self._run.article = ExpositoryArticle(
            title=translated["title"],
            summary=output["summary"],
            content_markdown=translated["content_markdown"],
            keywords=topic.get("keywords", []),
            main_category_id=topic.get("main_category_id"),
            sub_category_id=topic.get("sub_category_id"),
            topic=topic["topic"],
            core_argument=topic["core_argument"],
            english_title=self._output("5_write")["title"],
            pipeline_version=self._version_id,
        )
        await self._save()

    def _output(self, stage_key: str) -> dict[str, Any]:
        return self._run.stage_outputs[stage_key]

    def _verified_outline(self) -> dict[str, Any]:
        return self._output("4_outline_fact_check")["outline"]

    def _form(self) -> tuple[str, dict[str, Any]]:
        """이 글의 양식 (id, 양식). 1단계 결과가 고른 것이고 _topic 이 최신 버전에 있는 양식인지 먼저 검사한다."""
        form_id = self._output("1_topic_thesis")["form"]
        return form_id, self._forms[form_id]

    def _sections(self) -> list[str]:
        """글의 섹션 앵커 id 순서. 사실 확인을 거친 뼈대의 region 과 양식에서 나온다."""
        return section_ids(self._form()[1], self._verified_outline()["regions"])

    async def _run_stage(self, stage: Stage, inputs: dict[str, Any], check: Check) -> dict[str, Any]:
        """결과가 있고 프롬프트가 그 결과를 만든 프롬프트와 같으면 그 결과를 쓰고, 아니면 부른다."""
        prompt = render_prompt(self._prompts[stage.key], inputs)
        cached = self._run.stage_outputs.get(stage.key)
        if cached is not None and self._run.stage_prompts.get(stage.key) == prompt:
            return cached
        output = await self._call_until_valid(stage, inputs, check)
        await self._save_stage(stage, output, prompt)
        return output

    async def _save_stage(self, stage: Stage, output: dict[str, Any], prompt: str | None) -> None:
        # 결과와 프롬프트를 같이 쓴다. 프롬프트만 먼저 쓰면 호출이 실패했을 때 옛 결과가 새 프롬프트의 결과로 보인다
        self._run.stage_outputs[stage.key] = output
        if prompt is None:
            self._run.stage_prompts.pop(stage.key, None)
            self._run.stage_models.pop(stage.key, None)  # 사람이 준 결과다
        else:
            self._run.stage_prompts[stage.key] = prompt
            self._run.stage_models[stage.key] = self._models[stage.key]
        await self._save()

    async def _save(self) -> None:
        self._run.updated_at = datetime.now(UTC)
        await self._repository.upsert(self._run)

    async def _call_until_valid(self, stage: Stage, inputs: dict[str, Any], check: Check) -> dict[str, Any]:
        """검사를 통과한 출력. 걸리면 AgentCallService 가 직전 출력과 오류를 대화에 붙여 고치게 한다(처음부터 다시 쓰지 않게)."""
        prompt, model = self._prompts[stage.key], self._models[stage.key]
        # 프롬프트가 output_document 를 두면 JSON 이 아니라 "# 제목" + 본문 문서로 받는다. 결과 모양은 둘 다 title·content_markdown 이다
        as_document = "output_document" in prompt
        # 배치는 호출 모델의 가족이 고른다. 같은 프롬프트라도 벤더에 따라 다른 메시지로 나갈 수 있다 (D-103)
        layout = self._layouts.get(prompt_family(model), self._layouts[DEFAULT_LAYOUT_KEY])
        system, user = render_messages(prompt, inputs, layout)

        def parse(text: str) -> dict[str, Any]:
            try:
                output = parse_document(text) if as_document else parse_output(text)
            except ValueError as exc:
                raise LLMOutputError(str(exc)) from exc
            errors = check(output)
            if errors:
                raise LLMOutputError("\n".join(f"- {error}" for error in errors))
            return output

        # 스키마는 넘기지 않는다(call_text): 검색 단계는 구조화 출력과 같이 쓸 수 없고, 출력 검사는 parse 가 한다
        output, _ = await self._calls.call_text(
            kind=CallKind.EXPOSITORY,
            round_id=self._run.run_id,
            binding=model,
            messages=[ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            parse=parse,
            web_search=stage.web_search,
            item_id=stage.key,
            max_attempts=self._parameters.max_llm_retries,
            pipeline_version=self._version_id,
            retry_guidance=DOCUMENT_RETRY_GUIDANCE if as_document else RETRY_GUIDANCE,
        )
        return output

    async def _categories(self) -> list[dict[str, Any]]:
        if self._publisher is None:
            logger.warning("POST_BE_URL 이 없어 카테고리 목록 없이 주제를 고른다")
            return []
        options = category_options(await self._publisher.list_categories(), self._main_categories)
        return [option.model_dump() for option in options]


def build_publish_note(writer: str, fact_checkers: list[str], note: PublishNoteParameters) -> str:
    """글 끝 "AI Information" 표기 마크다운. 문구는 버전 문서의 조절 값(publish.note)이고 기존 라운드와 같은 모양이다 (D-87).

    작성 줄과, 사실 확인 모델이 있으면 리뷰 줄(모델(사실·구조))을 적는다. 요약·용어·번역 단계는 적지 않는다:
    표기 문구에 그 역할의 줄이 없다.
    """
    lines = ["---", "", note.heading, "", Template(note.writer).safe_substitute(models=writer)]
    if fact_checkers:
        label = note.lens_labels.get(_FACT_CHECK_LENS, _FACT_CHECK_LENS)
        models = ", ".join(f"{model}({label})" for model in dict.fromkeys(fact_checkers))
        lines.append(Template(note.reviewers).safe_substitute(models=models))
    return "\n".join(lines) + "\n"


def to_result(run: ExpositoryRun) -> ExpositoryResult:
    """실행 문서 → CLI 가 찍는 형태. 글이 끝났으면 제목·요약·본문을 한 덩어리 마크다운으로 만든다."""
    article = run.article
    return ExpositoryResult(
        run_id=run.run_id,
        finished_stages=[stage.key for stage in STAGES if stage.key in run.stage_outputs],
        pipeline_version=article.pipeline_version if article else None,
        title=article.title if article else None,
        summary=article.summary if article else None,
        markdown=f"# {article.title}\n\n> {article.summary}\n\n{article.content_markdown}" if article else None,
        external_post_id=run.publication.external_post_id if run.publication else None,
    )
