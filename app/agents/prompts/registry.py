"""프롬프트 템플릿 이름·가족과 조회 (설계 05 문서 머리말, 09 문서 1·4절).

본문은 파이프라인 버전 문서의 prompts(가족 → 이름 → 본문)에 있다(1a, D-71). 실행 중에는 파일을 읽지 않는다.
이 디렉터리의 v1~v4 는 기준 버전을 만드는 입력(bootstrap)과 비교 도구·테스트의 입력이다(load_prompt_files).
가족은 벤더가 아니라 모델 가족이다: Anthropic 의 haiku 는 변환 전용이라 opus 와 다른 지시문을 받는다.
가족에 없으면 common 을 본다. 평면 배치(v1~v3)는 common 으로 읽는다.
템플릿은 string.Template 문법($name)이다. JSON 예시의 중괄호와 충돌하지 않기 위해서다.
"""

from functools import lru_cache
from pathlib import Path

from app.models.enums import ReviewLens
from app.models.model_binding import CallModel
from app.models.pipeline import COMMON_FAMILY, PROMPT_FAMILIES, PromptSet
from app.models.routing import RoutingSnapshot

__all__ = [
    "COMMON_FAMILY",
    "PROMPT_FAMILIES",
    "TEMPLATE_NAMES",
    "MissingTemplateError",
    "load_prompt_files",
    "load_template",
    "prompt_family",
    "resolve_template",
    "templates_for_route",
    "validate_prompts",
]

TEMPLATE_NAMES = (
    "propose_topic",
    "write_draft",
    "revise_draft",
    "review_full",
    "review_spot_check",
    "arbitrate",
    "arbitrate_search",
    "fact_check",
    "fact_transform",
    "final_read",
    "assess_level",
    "polish_tone",
    "glossary",  # 용어 추출 (08 문서)
)

_PROMPTS_DIR = Path(__file__).parent
_FAMILY_BY_PROVIDER = {"anthropic": "claude", "google": "gemini", "xai": "grok"}
# 라우트가 쓰는 템플릿. 라운드를 만들 때 후보 모델의 가족마다 미리 확인해 글 중간에 빠진 템플릿을 만나지 않게 한다
_TEMPLATES_BY_ROUTE = {
    "PROPOSE_TOPIC": ("propose_topic",),
    "WRITE_DRAFT": ("write_draft",),
    "REVISE_DRAFT": ("revise_draft",),
    "ARBITRATE": ("arbitrate", "arbitrate_search"),
    "FINAL_READ": ("final_read",),
    "ASSESS_LEVEL": ("assess_level",),
    "POLISH_TONE": ("polish_tone",),
    "FACT_TRANSFORM": ("fact_transform",),
    "GLOSSARY": ("glossary",),
}
_REVIEW_TEMPLATES = ("review_full", "review_spot_check")  # 사실 확인 렌즈도 옛 글(슬롯 리뷰어)에서는 이 둘을 쓴다


class MissingTemplateError(LookupError):
    """버전 문서에 그 가족의 템플릿이 없다. 라운드 생성 전 검증(validate_prompts)이 먼저 잡는다."""


def prompt_family(model: CallModel) -> str:
    """호출 모델의 프롬프트 가족. 모르는 벤더는 벤더 이름이 가족이다: 그 가족이 없으면 common 으로 내려간다.
    벤더 자체의 검증은 LLM 클라이언트 레지스트리가 한다."""
    if model.provider == "anthropic" and "haiku" in model.model_id:
        return "haiku"
    return _FAMILY_BY_PROVIDER.get(model.provider, model.provider)


def resolve_template(prompts: PromptSet, name: str, family: str | None = None) -> str:
    """버전 문서의 prompts 에서 템플릿 본문. {family} → common 순으로 찾는다. 없는 이름은 ValueError, 없는 본문은 MissingTemplateError."""
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown prompt template {name}")
    for key in ([family] if family else []) + [COMMON_FAMILY]:
        text = prompts.get(key, {}).get(name)
        if text is not None:
            return text
    raise MissingTemplateError(f"prompt template {name} not found for family {family}")


@lru_cache
def load_template(name: str, version: str, family: str | None = None) -> str:
    """저장소 파일의 템플릿 원문. {version}/{family}/ → {version}/common/ → {version}/ 순. 실행 경로가 아니라 bootstrap·테스트용이다."""
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown prompt template {name}")
    base = _PROMPTS_DIR / version
    candidates = [base / family / f"{name}.md"] if family else []
    candidates += [base / COMMON_FAMILY / f"{name}.md", base / f"{name}.md"]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"prompt template {name} not found for version {version}, family {family}")


def load_prompt_files(version: str | Path) -> PromptSet:
    """저장소 프롬프트 디렉터리(또는 내보내기의 prompts/ 디렉터리)를 PromptSet 으로 읽는다.

    하위 디렉터리는 가족이고 루트의 평면 파일은 common 이다. TEMPLATE_NAMES 에 없는 파일(옛 polish_format 등)은 읽지 않는다.
    """
    base = (
        Path(version)
        if isinstance(version, Path) or "/" in str(version) or "\\" in str(version)
        else _PROMPTS_DIR / version
    )
    if not base.is_dir():
        raise FileNotFoundError(f"prompt directory {base} does not exist")
    prompts: PromptSet = {}
    for path in sorted(base.glob("*.md")):
        if path.stem in TEMPLATE_NAMES:
            prompts.setdefault(COMMON_FAMILY, {})[path.stem] = path.read_text(encoding="utf-8")
    for family_dir in sorted(each for each in base.iterdir() if each.is_dir()):
        for path in sorted(family_dir.glob("*.md")):
            if path.stem in TEMPLATE_NAMES:
                prompts.setdefault(family_dir.name, {})[path.stem] = path.read_text(encoding="utf-8")
    return prompts


def templates_for_route(route_id: str) -> tuple[str, ...]:
    """라우트 id 가 쓰는 템플릿 이름. 모르는 라우트(SLOTS, 옛 POLISH_FORMAT)는 비어 있다."""
    if route_id.startswith("REVIEW:"):
        lens = route_id.removeprefix("REVIEW:")
        return ("fact_check", *_REVIEW_TEMPLATES) if lens == ReviewLens.FACT_STRUCTURE else _REVIEW_TEMPLATES
    return _TEMPLATES_BY_ROUTE.get(route_id.split(":")[0], ())


def validate_prompts(snapshot: RoutingSnapshot, prompts: PromptSet) -> None:
    """스냅샷의 모든 라우트 후보가 쓸 템플릿이 prompts 에 있는지 확인한다. 없으면 ValueError (validate_snapshot 과 같은 원칙, R-05)."""
    missing: list[str] = []
    for route_id, candidates in snapshot.routes.items():
        for name in templates_for_route(route_id):
            for candidate in candidates:
                family = prompt_family(candidate)
                try:
                    resolve_template(prompts, name, family)
                except MissingTemplateError:
                    missing.append(f"{route_id}: {name} for {family} ({candidate.model_id})")
    if missing:
        raise ValueError("prompt set lacks templates: " + "; ".join(sorted(set(missing))))
