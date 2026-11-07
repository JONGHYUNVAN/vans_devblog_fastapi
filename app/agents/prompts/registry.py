"""프롬프트 템플릿 버전·가족과 로더 (설계 05 문서 머리말, 06 문서 2절).

버전 디렉터리는 배포 후 고치지 않는다. 문구를 바꾸면 새 디렉터리를 만들고 Settings.prompt_version 을 올린다.
v4 부터는 버전 안을 프롬프트 가족(claude, gemini, grok, haiku)으로 나눈다. 가족은 벤더가 아니라 모델 가족이다:
Anthropic 의 haiku 는 변환 전용이라 opus 와 다른 지시문을 받는다. 가족 디렉터리에 없으면 common/, 그다음 버전 루트(v1~v3 의 평면 배치)를 본다.
템플릿은 string.Template 문법($name)이다. JSON 예시의 중괄호와 충돌하지 않기 위해서다.
"""

from functools import lru_cache
from pathlib import Path

from app.models.enums import ReviewLens
from app.models.model_binding import CallModel
from app.models.routing import RoutingSnapshot

PROMPT_VERSION = "v4"  # 2026-11-07 2차 비교 뒤 올렸다(D-62). Settings.prompt_version 이 실제 값이다
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
    "glossary",  # 용어 추출 (08 문서). v4/haiku 와 v3 평면에 같은 파일. v3 의 새 이름은 기존 동작을 바꾸지 않는 추가다
)
PROMPT_FAMILIES = ("claude", "gemini", "grok", "haiku")
COMMON_FAMILY = "common"

_PROMPTS_DIR = Path(__file__).parent
_FAMILY_BY_PROVIDER = {"anthropic": "claude", "google": "gemini", "xai": "grok"}
# 라우트가 쓰는 템플릿. 라운드를 만들 때 후보 모델의 가족마다 미리 읽어 글 중간에 FileNotFoundError 를 만나지 않게 한다
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


def prompt_family(model: CallModel) -> str:
    """호출 모델의 프롬프트 가족. 모르는 벤더는 벤더 이름이 가족이다: 그 디렉터리가 없으면 common/·버전 루트로 내려가고,
    가족 전용 버전(v4)에서는 validate_prompts 가 라운드 생성 때 잡는다. 벤더 자체의 검증은 LLM 클라이언트 레지스트리가 한다."""
    if model.provider == "anthropic" and "haiku" in model.model_id:
        return "haiku"
    return _FAMILY_BY_PROVIDER.get(model.provider, model.provider)


@lru_cache
def load_template(name: str, version: str = PROMPT_VERSION, family: str | None = None) -> str:
    """템플릿 원문. {version}/{family}/ → {version}/common/ → {version}/ 순으로 찾는다. 없는 이름은 ValueError, 없는 파일은 FileNotFoundError."""
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown prompt template {name}")
    base = _PROMPTS_DIR / version
    candidates = [base / family / f"{name}.md"] if family else []
    candidates += [base / COMMON_FAMILY / f"{name}.md", base / f"{name}.md"]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"prompt template {name} not found for version {version}, family {family}")


def templates_for_route(route_id: str) -> tuple[str, ...]:
    """라우트 id 가 쓰는 템플릿 이름. 모르는 라우트(SLOTS, 옛 POLISH_FORMAT)는 비어 있다."""
    if route_id.startswith("REVIEW:"):
        lens = route_id.removeprefix("REVIEW:")
        return ("fact_check", *_REVIEW_TEMPLATES) if lens == ReviewLens.FACT_STRUCTURE else _REVIEW_TEMPLATES
    return _TEMPLATES_BY_ROUTE.get(route_id.split(":")[0], ())


def validate_prompts(snapshot: RoutingSnapshot, version: str) -> None:
    """스냅샷의 모든 라우트 후보가 쓸 템플릿이 있는지 라운드 생성 전에 확인한다. 없으면 ValueError (validate_snapshot 과 같은 원칙)."""
    missing: list[str] = []
    for route_id, candidates in snapshot.routes.items():
        for name in templates_for_route(route_id):
            for candidate in candidates:
                family = prompt_family(candidate)
                try:
                    load_template(name, version, family)
                except FileNotFoundError:
                    missing.append(f"{route_id}: {name} for {family} ({candidate.model_id})")
    if missing:
        raise ValueError(f"prompt version {version} lacks templates: " + "; ".join(sorted(set(missing))))
