"""표제어 키 정규화와 비슷한 표제어 찾기 (설계 08 문서 2.1절, 2.2절 ③). 순수 함수."""

import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

_STRIP = re.compile(r"[\s\-_·.]+")


def normalize_key(text: str) -> str:
    """NFKC → casefold → 공백·하이픈·밑줄·가운뎃점·마침표 제거. 영어·한국어 모두 같은 규칙이다."""
    return _STRIP.sub("", unicodedata.normalize("NFKC", text).casefold())


def headword_key_of(term_ko: str, term_en: str | None) -> str:
    """표제어 키. term_en 의 키이고, 없으면 term_ko 의 키다."""
    return normalize_key(term_en) if term_en and normalize_key(term_en) else normalize_key(term_ko)


def _bigrams(key: str) -> set[str]:
    if len(key) < 2:
        return {key}
    return {key[index : index + 2] for index in range(len(key) - 1)}


def similarity(a: str, b: str) -> float:
    """문자 2-gram Jaccard 와 편집 유사도의 가중 평균. 키 기준이라 공백·대소문자 차이는 이미 없다."""
    if not a or not b:
        return 0.0
    grams_a, grams_b = _bigrams(a), _bigrams(b)
    jaccard = len(grams_a & grams_b) / len(grams_a | grams_b)
    return 0.7 * jaccard + 0.3 * SequenceMatcher(None, a, b).ratio()


def similar_headwords(key: str, candidates: Iterable[str], *, limit: int = 8, threshold: float = 0.3) -> list[str]:
    """key 와 비슷한 후보 키를 유사도 순으로. 같은 키는 뺀다. 사람 참고용이고 등록 판정에는 쓰지 않는다."""
    scored = [(similarity(key, candidate), candidate) for candidate in set(candidates) if candidate != key]
    ranked = sorted((score, candidate) for score, candidate in scored if score >= threshold)
    return [candidate for _score, candidate in sorted(ranked, key=lambda pair: (-pair[0], pair[1]))[:limit]]
