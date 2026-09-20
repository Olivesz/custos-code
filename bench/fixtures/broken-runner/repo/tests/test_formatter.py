from widgets.formatter import format_amount


def test_format_amount() -> None:
    assert format_amount(150) == "$1.50"
