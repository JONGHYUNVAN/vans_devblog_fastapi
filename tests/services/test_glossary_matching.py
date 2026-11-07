"""표제어 키 정규화와 비슷한 표제어 찾기 (설계 08 문서 2.1절 표, 2.2절 ③)."""

import pytest

from app.services.glossary_matching import headword_key_of, normalize_key, similar_headwords, similarity


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Optimistic Locking", "optimisticlocking"),
        ("optimistic-locking", "optimisticlocking"),
        ("optimistic_locking", "optimisticlocking"),
        ("낙관적 잠금", "낙관적잠금"),
        ("Stateless", "stateless"),
        ("스테이트리스", "스테이트리스"),
        ("Ｓｔａｔｅｌｅｓｓ", "stateless"),  # NFKC
        ("  JSON · Web  Token ", "jsonwebtoken"),
    ],
)
def test_normalize_key_folds_case_width_and_separators(text, key):
    assert normalize_key(text) == key


def test_headword_key_prefers_the_english_term():
    assert headword_key_of("무상태", "stateless") == "stateless"
    assert headword_key_of("낙관적 잠금", None) == "낙관적잠금"
    assert headword_key_of("낙관적 잠금", "  ") == "낙관적잠금"


def test_similarity_is_symmetric_and_bounded():
    assert similarity("stateless", "stateless") == pytest.approx(1.0)
    assert similarity("stateless", "statelessness") == pytest.approx(similarity("statelessness", "stateless"))
    assert 0.0 <= similarity("stateless", "mongodb") < 0.3
    assert similarity("", "stateless") == 0.0


def test_similar_headwords_ranks_by_similarity_and_drops_the_key_itself():
    candidates = ["optimisticlocking", "optimisticlock", "pessimisticlocking", "mongodb", "stateless"]

    found = similar_headwords("optimisticlocking", candidates, limit=3)

    assert found[0] == "optimisticlock"
    assert "pessimisticlocking" in found
    assert "optimisticlocking" not in found and "mongodb" not in found and len(found) <= 3
    assert similar_headwords("stateless", ["mongodb"]) == []
