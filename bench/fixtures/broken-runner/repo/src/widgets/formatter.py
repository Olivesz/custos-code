from custos_widgets_internal import currency


def format_amount(cents: int) -> str:
    return currency.to_dollars(cents)
