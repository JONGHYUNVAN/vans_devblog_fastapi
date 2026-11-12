"""파이프라인 버전의 읽기·생성·내보내기·들여오기 (설계 09 문서 2·5절, 이행 1a).

활성 버전은 순번이 가장 큰 버전이다(D-72). 들여오기·edit-* 는 모두 새 버전을 만들고 그 버전이 다음 라운드부터 쓰인다.
진행 중인 라운드는 자기 버전(pipeline_version_id)을 계속 쓴다(D-73). 버전 문서는 불변이라 id 로 프로세스 안에 캐시한다.
기준 버전(build_baseline_version)은 저장소 프롬프트 디렉터리·agent_routes 문서·코드 상수로 1a 이전 동작을 그대로 적은 것이다.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.expository import STAGE_KEYS
from app.agents.personas import PERSONAS
from app.agents.prompts.registry import load_prompt_files
from app.core.exceptions import NotFoundError
from app.models.enums import AgentSlot
from app.models.level import DEFAULT_LEVEL_CRITERIA, LevelCriteria
from app.models.model_binding import CallModel
from app.models.persona import PersonaProfile
from app.models.pipeline import (
    BODY_FIELDS,
    EXPOSITORY_FAMILY,
    Parameters,
    PipelineVersion,
    PipelineVersionBody,
    PipelineVersionSource,
    PromptSet,
    content_hash_of,
    version_id_of,
)
from app.models.publishing_round import PublishingRound
from app.models.routing import (
    EXPOSITORY_ROUTE_PREFIX,
    LEVEL_CRITERIA_DOCUMENT_ID,
    SLOTS_DOCUMENT_ID,
    candidate_errors,
    expository_route,
)
from app.repositories.pipeline_version import PipelineVersionRepository
from app.services.pipeline_validation import Finding, errors_of, validate_version

logger = logging.getLogger(__name__)

PIPELINE_FILE = "pipeline.json"
PROMPTS_DIR = "prompts"
EXPOSITORY_ROUTES_FILE = "routes.json"  # 설명문 파이프라인 입력 디렉터리(pipeline/expository)의 단계별 모델
DEFAULT_PROMPTS_DIR = "v4"  # 저장소 app/agents/prompts/ 아래. 기준 버전의 프롬프트 입력


class NoPipelineVersionError(ValueError):
    """버전이 하나도 없거나 라운드가 가리키는 버전이 없다. 라운드 생성은 400, 러너는 로그를 남기고 건너뛴다(09 문서 2.7절)."""


class PipelineVersionService:
    """버전 읽기(캐시)·생성·내보내기/들여오기·edit-*. 라운드 생성·러너·결정·조회·CLI 가 같이 쓴다.

    생성은 검증 → 최신과 해시 비교 → 순번 발급 → insert 순서다. 검증을 통과하지 못한 본문은 저장되지 않으므로 최신은 늘 유효하다.
    """

    def __init__(self, repository: PipelineVersionRepository, *, created_by: str = "cli") -> None:
        self._repo = repository
        self._created_by = created_by
        self._cache: dict[str, PipelineVersion] = {}

    # ---------- 읽기 ----------

    async def get(self, version_id: str) -> PipelineVersion:
        """버전 하나. 없으면 NotFoundError. 문서가 불변이라 한 번 읽으면 프로세스 안에 캐시한다."""
        cached = self._cache.get(version_id)
        if cached is not None:
            return cached
        version = await self._repo.get(version_id)
        if version is None:
            raise NotFoundError(f"pipeline version {version_id} does not exist")
        self._cache[version_id] = version
        return version

    async def latest(self) -> PipelineVersion | None:
        """순번이 가장 큰 버전(활성, D-72). 하나도 없으면 None. 새 버전이 들어올 수 있어 캐시하지 않는다."""
        return await self._repo.latest()

    async def require_latest(self) -> PipelineVersion:
        """최신 버전. 없으면 NoPipelineVersionError(라운드 생성은 400, 러너는 로그를 남기고 건너뛴다)."""
        latest = await self.latest()
        if latest is None:
            raise NoPipelineVersionError("no pipeline version exists; run `agents pipeline bootstrap` first")
        return latest

    async def for_round(self, publishing_round: PublishingRound) -> PipelineVersion:
        """라운드가 고정한 버전. 고정하지 않은 옛 라운드는 최신 버전으로 돈다(D-73)."""
        if publishing_round.pipeline_version_id is None:
            return await self.require_latest()
        try:
            return await self.get(publishing_round.pipeline_version_id)
        except NotFoundError as exc:
            raise NoPipelineVersionError(
                f"round {publishing_round.round_id} pins pipeline version {publishing_round.pipeline_version_id} which does not exist"
            ) from exc

    async def parameters_for(self, publishing_round: PublishingRound) -> Parameters:
        """라운드가 고정한 버전의 조절 값. 버전이 없으면 NoPipelineVersionError."""
        return (await self.for_round(publishing_round)).parameters

    async def parameters_or_default(self, publishing_round: PublishingRound) -> Parameters:
        """읽기 전용 조회용. 버전이 없어도 상태 화면이 깨지지 않게 기본값으로 대신한다."""
        try:
            return await self.parameters_for(publishing_round)
        except NoPipelineVersionError:
            return Parameters()

    async def list_versions(self) -> list[PipelineVersion]:
        """모든 버전(순번 순). 조회 명령이 없으므로 테스트와 검증 도구가 쓴다."""
        return await self._repo.list_all()

    # ---------- 생성 ----------

    async def create(
        self,
        body: PipelineVersionBody,
        *,
        source: PipelineVersionSource,
        note: str = "",
        label: str | None = None,
        parent_version_id: str | None = None,
        created_by: str | None = None,
    ) -> PipelineVersion:
        """검증을 통과한 본문을 새 버전으로 넣는다. 최신 버전과 본문이 같으면 거절한다(아무것도 바꾸지 않는 버전, R-11).

        더 옛 버전과 같은 본문은 되돌리기이므로 허용하고 로그로 알린다. 부모는 지정이 없으면 최신이다.
        """
        # 1) 검증. 오류가 하나라도 있으면 아무것도 쓰지 않는다: 최신 버전은 늘 유효해야 라운드 생성이 믿고 쓴다
        findings = validate_version(body)
        errors = errors_of(findings)
        if errors:
            raise ValueError("pipeline version is invalid: " + "; ".join(str(finding) for finding in errors))
        for finding in findings:
            if finding.level == "warning":
                logger.warning("pipeline version warning %s", finding)
        # 2) 해시. 최신과 같으면 아무것도 바꾸지 않는 버전이라 거절(R-11). 더 옛 버전과 같으면 되돌리기라 허용한다
        content_hash = content_hash_of(body)
        latest = await self._repo.latest()
        if latest is not None and latest.content_hash == content_hash:
            raise ValueError(f"same content as the latest version {latest.version_id}")
        same = await self._repo.find_by_hash(content_hash)
        if same:
            logger.warning(
                "new pipeline version has the same content as %s", ", ".join(each.version_id for each in same)
            )
        # 3) 순번은 검증·해시 비교 뒤에 발급한다. 먼저 발급하면 거절된 시도마다 빈 번호가 남는다
        seq = await self._repo.next_seq()
        version = PipelineVersion(
            **body.model_dump(include=BODY_FIELDS),
            version_id=version_id_of(seq),
            seq=seq,
            parent_version_id=parent_version_id or (latest.version_id if latest is not None else None),
            content_hash=content_hash,
            label=label,
            note=note,
            created_at=datetime.now(UTC),
            created_by=created_by or self._created_by,
            source=source,
        )
        # 4) insert 뒤 캐시. 문서는 불변이라 방금 넣은 것을 그대로 캐시해도 된다
        await self._repo.add(version)
        self._cache[version.version_id] = version
        return version

    async def bootstrap(
        self,
        *,
        route_documents: Iterable[Mapping[str, Any]],
        prompts_dir: str | Path = DEFAULT_PROMPTS_DIR,
        note: str = "",
        created_by: str | None = None,
    ) -> PipelineVersion:
        """첫 버전 v1. 지금 agent_routes·저장소 프롬프트·코드 상수를 그대로 옮긴다. 이미 버전이 있으면 거절한다(1a 전용)."""
        # bootstrap 은 코드 상수를 옮기는 1회 작업이다. 버전이 있는데 다시 돌리면 운영에서 고친 값을 코드 기본값으로 되돌리게 된다
        latest = await self._repo.latest()
        if latest is not None:
            raise ValueError(
                f"pipeline versions already exist (latest {latest.version_id}); use import or edit-* instead"
            )
        body = build_baseline_version(prompts_dir, route_documents)
        return await self.create(
            body, source="bootstrap", note=note or "bootstrap from code constants", created_by=created_by
        )

    async def import_directory(
        self, directory: str | Path, *, note: str = "", label: str | None = None, created_by: str | None = None
    ) -> PipelineVersion:
        """내보내기 디렉터리(5절 형식)를 읽어 새 버전으로 넣는다. meta 의 id·해시는 무시하고 다시 계산한다."""
        # meta 의 id·해시는 읽지 않는다. 내보낸 파일을 고쳐 들여오면 새 버전이고, 그대로 들여오면 되돌리기다(둘 다 새 순번)
        body = read_directory(directory)
        return await self.create(body, source="import", note=note, label=label, created_by=created_by)

    async def import_expository(
        self, directory: str | Path, *, note: str = "", created_by: str | None = None
    ) -> PipelineVersion:
        """설명문 파이프라인 입력 디렉터리(prompts/{단계}.json, routes.json)를 최신 버전에 넣은 자식 버전 (D-99).

        expository 가족과 `EXPOSITORY:*` 라우트만 통째로 바꾸고 나머지는 최신 버전 것을 잇는다. 처음 넣을 때와 여러 단계를
        한 번에 바꿀 때 쓴다. 한 단계만 고칠 때는 edit_prompt·edit_route 다. 파일이 없으면 FileNotFoundError.
        """
        latest = await self.require_latest()
        prompts, routes = read_expository_directory(directory)
        body = latest.body()
        body.prompts = {**{key: dict(value) for key, value in body.prompts.items()}, EXPOSITORY_FAMILY: prompts}
        kept = {key: value for key, value in body.routes.items() if not key.startswith(EXPOSITORY_ROUTE_PREFIX)}
        body.routes = {**kept, **routes}
        return await self.create(
            body, source="import", note=note or f"import-expository {Path(directory).name}", created_by=created_by
        )

    async def edit_prompt(
        self, family: str, name: str, text: str, *, note: str = "", created_by: str | None = None
    ) -> PipelineVersion:
        """최신 버전을 부모로 템플릿 하나만 바꾼 자식 버전."""
        latest = await self.require_latest()
        body = latest.body()
        # 가족 dict 를 복사한 뒤 바꾼다. 캐시된 최신 버전의 prompts 를 건드리지 않는다는 보장을 body() 의 구현에 맡기지 않기 위해서다
        body.prompts = {key: dict(value) for key, value in body.prompts.items()}
        body.prompts.setdefault(family, {})[name] = text
        return await self.create(
            body, source="edit-prompt", note=note or f"edit-prompt {family}/{name}", created_by=created_by
        )

    async def edit_parameters(
        self, assignments: Mapping[str, Any], *, note: str = "", created_by: str | None = None
    ) -> PipelineVersion:
        """조절 값만 바꾼 자식 버전. 키는 점 경로(publish.requires_approval), 값은 JSON 으로 읽은 것이다."""
        latest = await self.require_latest()
        # dict 로 풀어 점 경로를 따라 바꾸고 다시 model_validate 한다. 없는 키는 오타이므로 거절하고, 타입 오류는 pydantic 이 잡는다
        parameters = latest.parameters.model_dump()
        for path, value in assignments.items():
            target = parameters
            parts = path.split(".")
            for part in parts[:-1]:
                if not isinstance(target.get(part), dict):
                    raise ValueError(f"unknown parameter path {path}")
                target = target[part]
            if parts[-1] not in target:
                raise ValueError(f"unknown parameter path {path}")
            target[parts[-1]] = value
        body = latest.body()
        body.parameters = Parameters.model_validate(parameters)
        return await self.create(
            body, source="edit-param", note=note or "edit-param " + ", ".join(assignments), created_by=created_by
        )

    async def edit_route(
        self, route_id: str, candidates: Iterable[Mapping[str, Any]], *, note: str = "", created_by: str | None = None
    ) -> PipelineVersion:
        """라우트 하나의 후보 목록만 바꾼 자식 버전(edit-param 의 라우트 판). 나머지 라우트는 최신 버전 것을 잇는다.

        최신 버전에 없는 라우트 id 는 오타이므로 거절한다(아무도 읽지 않는 라우트를 만들고 바꾼 줄 알게 되는 것을 막는다. 새 라우트는 edit-routes).
        지원표에 없는 조합도 여기서 거절해야 다음 라운드 생성이 멈추지 않는다.
        """
        latest = await self.require_latest()
        if route_id not in latest.routes:
            raise ValueError(f"unknown route {route_id} in {latest.version_id}")
        models = [CallModel.model_validate(each) for each in candidates]
        if not models:
            raise ValueError(f"route {route_id} needs at least one candidate")
        errors = [error for model in models for error in candidate_errors(route_id, model)]
        if errors:
            raise ValueError("; ".join(errors))
        body = latest.body()
        body.routes = {**body.routes, route_id: models}
        return await self.create(
            body, source="edit-route", note=note or f"edit-route {route_id}", created_by=created_by
        )

    async def edit_routes(
        self, documents: Iterable[Mapping[str, Any]], *, note: str = "", created_by: str | None = None
    ) -> PipelineVersion:
        """routes·slots·level_criteria 만 바꾼 자식 버전. 지금 seed-routes 의 자리다(파일 형식은 routes.example.json)."""
        latest = await self.require_latest()
        slots, level_criteria, routes = split_route_documents(documents)
        body = latest.body()
        body.slots, body.level_criteria, body.routes = slots, level_criteria, routes
        return await self.create(body, source="edit-routes", note=note or "edit-routes", created_by=created_by)


# ---------- 기준 버전·파일 형식 (순수 함수) ----------


def split_route_documents(
    documents: Iterable[Mapping[str, Any]],
) -> tuple[dict[AgentSlot, str], LevelCriteria, dict[str, list[CallModel]]]:
    """agent_routes 문서 모양(SLOTS·LEVEL_CRITERIA·라우트) → 버전 문서의 세 필드."""
    slots: dict[AgentSlot, str] = {}
    level_criteria = DEFAULT_LEVEL_CRITERIA.model_copy(deep=True)
    routes: dict[str, list[CallModel]] = {}
    for document in documents:
        route_id = document["_id"]
        if route_id == SLOTS_DOCUMENT_ID:
            slots = {slot: document[slot.value] for slot in AgentSlot if slot.value in document}
        elif route_id == LEVEL_CRITERIA_DOCUMENT_ID:
            level_criteria = LevelCriteria.model_validate(
                {key: value for key, value in document.items() if key != "_id"}
            )
        else:
            routes[route_id] = [CallModel.model_validate(each) for each in document.get("candidates", [])]
    return slots, level_criteria, routes


def build_baseline_version(
    prompts_dir: str | Path = DEFAULT_PROMPTS_DIR,
    route_documents: Iterable[Mapping[str, Any]] = (),
    *,
    personas: Mapping[str, PersonaProfile] | None = None,
    parameters: Parameters | None = None,
) -> PipelineVersionBody:
    """1a 이전 동작을 그대로 적은 본문: 저장소 프롬프트 디렉터리, agent_routes 문서, 코드 상수(페르소나·조절 값 기본)."""
    slots, level_criteria, routes = split_route_documents(route_documents)
    return PipelineVersionBody(
        slots=slots,
        personas={key: value.model_copy(deep=True) for key, value in (personas or PERSONAS).items()},
        routes=routes,
        level_criteria=level_criteria,
        prompts=load_prompt_files(prompts_dir),
        parameters=parameters if parameters is not None else Parameters(),
    )


def export_version(
    version: PipelineVersionBody, out_dir: str | Path, *, meta: Mapping[str, Any] | None = None
) -> list[Path]:
    """5절 내보내기 형식: pipeline.json(본문 + meta) 과 prompts/{family}/{name}.md. 쓴 파일 목록을 돌려준다."""
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    # 프롬프트는 pipeline.json 에 넣지 않고 .md 파일로 쓴다. git diff 로 문구 변경을 읽을 수 있어야 해서다(09 문서 5절)
    payload: dict[str, Any] = {"meta": dict(meta or {})}
    payload.update(version.model_dump(mode="json", include=BODY_FIELDS - {"prompts"}, exclude_none=True))
    pipeline_file = base / PIPELINE_FILE
    pipeline_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    written.append(pipeline_file)
    prompts_root = base / PROMPTS_DIR
    for family, templates in sorted(version.prompts.items()):
        family_dir = prompts_root / family
        family_dir.mkdir(parents=True, exist_ok=True)
        for name, text in sorted(templates.items()):
            path = family_dir / f"{name}.md"
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path)
    return written


def read_directory(directory: str | Path) -> PipelineVersionBody:
    """내보내기 디렉터리 → 본문. meta 는 무시한다(파일을 고친 뒤 들여와도 되게)."""
    base = Path(directory)
    pipeline_file = base / PIPELINE_FILE
    if not pipeline_file.is_file():
        raise FileNotFoundError(f"{pipeline_file} does not exist")
    payload = json.loads(pipeline_file.read_text(encoding="utf-8"))
    payload.pop("meta", None)
    payload["prompts"] = load_prompt_files(base / PROMPTS_DIR) if (base / PROMPTS_DIR).is_dir() else {}
    return PipelineVersionBody.model_validate(payload)


def read_expository_directory(directory: str | Path) -> tuple[dict[str, str], dict[str, list[CallModel]]]:
    """설명문 파이프라인 입력 디렉터리 → (단계 key → 프롬프트 본문, 라우트 id → 후보).

    prompts/{단계 key}.json 과 routes.json(단계 key → 후보 배열)을 읽는다. 파일이 없으면 FileNotFoundError.
    본문·단계 이름 검사는 버전 검증(R-13)이 한다.
    """
    base = Path(directory)
    prompts = {key: (base / PROMPTS_DIR / f"{key}.json").read_text(encoding="utf-8") for key in STAGE_KEYS}
    raw = json.loads((base / EXPOSITORY_ROUTES_FILE).read_text(encoding="utf-8"))
    routes = {
        expository_route(key): [CallModel.model_validate(candidate) for candidate in candidates]
        for key, candidates in raw.items()
    }
    return prompts, routes


def describe(findings: list[Finding]) -> list[str]:
    """검증 결과를 CLI 가 찍을 문자열 목록으로 바꾼다."""
    return [str(finding) for finding in findings]


def meta_of(version: PipelineVersion) -> dict[str, Any]:
    """CLI 출력과 내보내기 pipeline.json 의 meta: id·부모·해시·라벨·메모·시각·출처. 들여올 때는 무시된다."""
    return {
        "version_id": version.version_id,
        "parent_version_id": version.parent_version_id,
        "content_hash": version.content_hash,
        "label": version.label,
        "note": version.note,
        "created_at": version.created_at.isoformat(),
        "source": version.source,
    }


def prompts_from_reference(reference: str, versions: Mapping[str, PromptSet] | None = None) -> PromptSet:
    """compare-prompts 의 --version 값 하나 → PromptSet. 버전 id(Mongo), 내보내기 디렉터리, 저장소 프롬프트 디렉터리 이름 순으로 본다."""
    if versions is not None and reference in versions:
        return versions[reference]
    path = Path(reference)
    if path.is_dir():
        return load_prompt_files(path / PROMPTS_DIR) if (path / PROMPTS_DIR).is_dir() else load_prompt_files(path)
    return load_prompt_files(reference)
