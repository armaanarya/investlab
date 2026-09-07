"""Decimal money and share-count arithmetic.

Float is never used for money anywhere in this project. Every boundary
quantizes to cents. Share counts are integers and are never rounded up.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

Money = Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _coerce(value: int | str | Decimal) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"money must never come from {type(value).__name__}; use Decimal or str")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"cannot build money from {type(value).__name__}")


def usd(value: int | str | Decimal) -> Money:
    """Quantize to cents, ROUND_HALF_UP. The default money constructor."""
    return _coerce(value).quantize(CENT, rounding=ROUND_HALF_UP)


def usd_ceil(value: int | str | Decimal) -> Money:
    """Quantize to cents, always upward. For costs and fees only."""
    return _coerce(value).quantize(CENT, rounding=ROUND_CEILING)


def round_shares(value: int | str | Decimal) -> int:
    """Round a share count DOWN, at any sign. Never up, ever."""
    return int(_coerce(value).to_integral_value(rounding=ROUND_FLOOR))
