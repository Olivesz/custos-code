def median(values: list[int]) -> int:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    return ordered[0]
