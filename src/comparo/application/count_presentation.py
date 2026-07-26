"""Bounded, conversion-safe presentation for caller-owned exact counts."""

_MAXIMUM_GROUPED_BITS = 80


def count_label(value: int) -> str:
    """Render ordinary counts exactly and extreme counts as a bounded magnitude.

    Python intentionally rejects decimal conversion of extremely large integers. Comparo
    accepts caller-owned budgets, so a presentation boundary must never feed an arbitrary
    admitted integer to ``str`` or JSON encoding.
    """
    if type(value) is not int or value < 0:
        raise ValueError("count presentation requires a non-negative exact integer")
    if value.bit_length() <= _MAXIMUM_GROUPED_BITS:
        return f"{value:,}"
    return f"≥2^{value.bit_length() - 1} (exact value retained)"


__all__ = ("count_label",)
