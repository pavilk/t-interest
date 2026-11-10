import pytest

from app.schemas.survey import MatchingProfile
from app.services.matching import jaccard, score


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(set(), set(), 0), ({1}, set(), 0), ({1}, {2}, 0), ({1}, {1}, 1), ({1, 2}, {2, 3}, 1 / 3)],
)
def test_jaccard(a: set[int], b: set[int], expected: float) -> None:
    assert jaccard(a, b) == pytest.approx(expected)
    assert jaccard(b, a) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("a_ids", "b_ids", "office", "city", "expected"),
    [
        ({1}, {1}, 1, "Город", 1.15),
        ({1}, {1}, 2, "Город", 1.05),
        ({1}, {1}, 2, "Другой", 1.0),
        (set(), set(), 1, "Город", 0.15),
        (set(), set(), 2, "Город", 0.05),
        (set(), set(), 2, "Другой", 0.0),
        ({1, 2}, {2, 3}, 2, "Другой", 0.333),
    ],
)
def test_score(a_ids: set[int], b_ids: set[int], office: int, city: str, expected: float) -> None:
    a = MatchingProfile(interest_ids=a_ids, office_id=1, city="Город")
    b = MatchingProfile(interest_ids=b_ids, office_id=office, city=city)
    assert score(a, b) == expected
    assert score(b, a) == expected
