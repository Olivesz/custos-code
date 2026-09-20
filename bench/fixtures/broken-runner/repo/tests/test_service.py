import missing_fixture_dependency
from src.service import status


def test_status() -> None:
    assert missing_fixture_dependency is not None
    assert status() == "ok"
