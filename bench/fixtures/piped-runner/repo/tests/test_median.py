from src.stats import median


def test_median_of_odd_count() -> None:
    assert median([7, 1, 3]) == 3
