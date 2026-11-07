"""표제어 키 정규화와 비슷한 표제어 찾기 (설계 08 문서 2.1절 "정규화 키", 2.2절 ③). 순수 함수.

같은 용어가 대소문자·하이픈·공백·전각 차이로 다른 표제어가 되지 않게 하는 한 규칙이다. 결과는 headword_key 와 lookup_keys 로
저장되고 unique 인덱스의 값이므로, 규칙을 바꾸면 기존 키와 어긋난다(`agents glossary check` 가 stale 로 잡는다). 바꾸려면
재계산이 먼저다. 유사도는 등록 판정에 쓰지 않고(D-65) 사람 참고용 후보(similar_headwords)와 dedupe 에만 쓴다.
"""

import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

# 표기 차이로만 갈리는 문자. 마침표는 "Node.js"/"nodejs", 가운뎃점은 한국어 병기("키·값") 때문이다. 슬래시·괄호는 뜻을 바꿀 수 있어 남긴다
_STRIP = re.compile(r"[\s\-_·.]+")


def normalize_key(text: str) -> str:
    """NFKC → casefold → 공백·하이픈·밑줄·가운뎃점·마침표 제거. 영어·한국어 모두 같은 규칙이다."""
    return _STRIP.sub("", unicodedata.normalize("NFKC", text).casefold())


def headword_key_of(term_ko: str, term_en: str | None) -> str:
    """표제어 키. term_en 의 키이고, 없으면 term_ko 의 키다."""
    return normalize_key(term_en) if term_en and normalize_key(term_en) else normalize_key(term_ko)


def _bigrams(key: str) -> set[str]:
    if len(key) < 2:
        return {key}  # 한 글자 키는 자기 자신이 유일한 조각이다. 빈 집합이면 아래 Jaccard 가 0 으로 나눈다
    return {key[index : index + 2] for index in range(len(key) - 1)}


def similarity(a: str, b: str) -> float:
    """문자 2-gram Jaccard 와 편집 유사도의 가중 평균. 키 기준이라 공백·대소문자 차이는 이미 없다."""
    if not a or not b:
        return 0.0
    grams_a, grams_b = _bigrams(a), _bigrams(b)
    jaccard = len(grams_a & grams_b) / len(grams_a | grams_b)
    # 2-gram 이 주다: 키는 짧고 공백이 없어 단어 단위 비교가 안 되고, 한국어 음차 차이(트랜잭션/트랜섹션)를 잘 잡는다.
    # 편집 유사도는 조각이 같은데 순서가 다른 경우("dbindex"/"indexdb")를 눌러 준다
    return 0.7 * jaccard + 0.3 * SequenceMatcher(None, a, b).ratio()


def similar_headwords(key: str, candidates: Iterable[str], *, limit: int = 8, threshold: float = 0.3) -> list[str]:
    """key 와 비슷한 후보 키를 유사도 순으로. 같은 키는 뺀다. 사람 참고용이고 등록 판정에는 쓰지 않는다."""
    # threshold 0.3 은 짧은 키끼리 우연히 겹치는 2-gram 을 거르는 하한이고, limit 8 은 사람이 훑을 분량이다(08 문서 ③ "5~10개")
    scored = [(similarity(key, candidate), candidate) for candidate in set(candidates) if candidate != key]
    ranked = sorted((score, candidate) for score, candidate in scored if score >= threshold)
    return [candidate for _score, candidate in sorted(ranked, key=lambda pair: (-pair[0], pair[1]))[:limit]]
