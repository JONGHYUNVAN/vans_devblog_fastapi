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

from app.agents.personas import PERSONAS
from app.agents.prompts.registry import load_prompt_files
from app.core.exceptions import NotFoundError
from app.models.enums import AgentSlot
from app.models.level import DEFAULT_LEVEL_CRITERIA, LevelCriteria
from app.models.model_binding import CallModel
from app.models.persona import PersonaProfile
from app.models.pipeline import (
    BODY_FIELDS,
    Parameters,
    PipelineVersion,
    PipelineVersionBody,
    PipelineVersionSource,
    PromptSet,
    content_hash_of,
    version_id_of,
)
from app.models.publishing_round import PublishingRound
from app.models.routing import LEVEL_CRITERIA_DOCUMENT_ID, SLOTS_DOCUMENT_ID
from app.repositories.pipeline_version import PipelineVersionRepository
from app.services.pipeline_validation import Finding, errors_of, validate_version

logger = logging.getLogger(__name__)

PIPELINE_FILE = "pipeline.json"
PROMPTS_DIR = "prompts"
DEFAULT_PROMPTS_DIR = "v4"  # 저장소 app/agents/prompts/ 아래. 기준 버전의 프롬프트 입력


class NoPipelineVersionError(ValueError):
    """버전이 하나도 없거나 라운드가 가리키는 버전이 없다. 라운드 생성은 400, 러너는 로그를 남기고 건너뛴다(09 문서 2.7절)."""


class PipelineVersionService:
    def __init__(self, repository: PipelineVersionRepository, *, created_by: str = "cli") -> None:
        self._repo = repository
        self._created_by = created_by
        self._cache: dict[str, PipelineVersion] = {}

    # ---------- 읽기 ----------

    async def get(self, version_id: str) -> PipelineVersion:
        cached = self._cache.get(version_id)
        if cached is not None:
            return cached
        version = await self._repo.get(version_id)
        if version is None:
            raise NotFoundError(f"pipeline version {version_id} does not exist")
        self._cache[version_id] = version
        return version

    async def latest(self) -> PipelineVersion | None:
        return await self._repo.latest()

    async def require_latest(self) -> PipelineVersion:
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
        return (await self.for_round(publishing_round)).parameters

    async def parameters_or_default(self, publishing_round: PublishingRound) -> Parameters:
        """읽기 전용 조회용. 버전이 없어도 상태 화면이 깨지지 않게 기본값으로 대신한다."""
        try:
            return await self.parameters_for(publishing_round)
        except NoPipelineVersionError:
            return Parameters()

    async def list_versions(self) -> list[PipelineVersion]:
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
        findings = validate_version(body)
        errors = errors_of(findings)
        if errors:
            raise ValueError("pipeline version is invalid: " + "; ".join(str(finding) for finding in errors))
        for finding in findings:
            if finding.level == "warning":
                logger.warning("pipeline version warning %s", finding)
        content_hash = content_hash_of(body)
        latest = await self._repo.latest()
        if latest is not None and latest.content_hash == content_hash:
            raise ValueError(f"same content as the latest version {latest.version_id}")
        same = await self._repo.find_by_hash(content_hash)
        if same:
            logger.warning(
                "new pipeline version has the same content as %s", ", ".join(each.version_id for each in same)
            )
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
        body = read_directory(directory)
        return await self.create(body, source="import", note=note, label=label, created_by=created_by)

    async def edit_prompt(
        self, family: str, name: str, text: str, *, note: str = "", created_by: str | None = None
    ) -> PipelineVersion:
        """최신 버전을 부모로 템플릿 하나만 바꾼 자식 버전."""
        latest = await self.require_latest()
        body = latest.body()
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


def describe(findings: list[Finding]) -> list[str]:
    return [str(finding) for finding in findings]


def meta_of(version: PipelineVersion) -> dict[str, Any]:
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
