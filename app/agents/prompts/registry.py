"""프롬프트 템플릿 버전과 로더 (설계 05 문서 머리말, 06 문서 2절).

버전 디렉터리는 배포 후 고치지 않는다. 문구를 바꾸면 새 디렉터리(v2)를 만들고 Settings.prompt_version 을 올린다.
템플릿은 string.Template 문법($name)이다. JSON 예시의 중괄호와 충돌하지 않기 위해서다.
"""

from functools import lru_cache
from pathlib import Path

PROMPT_VERSION = "v3"
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
    "polish_format",
    "polish_tone",
)

_PROMPTS_DIR = Path(__file__).parent


@lru_cache
def load_template(name: str, version: str = PROMPT_VERSION) -> str:
    """템플릿 원문. 없는 이름·버전은 설정 오류라 FileNotFoundError 를 그대로 둔다."""
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown prompt template {name}")
    return (_PROMPTS_DIR / version / f"{name}.md").read_text(encoding="utf-8")
