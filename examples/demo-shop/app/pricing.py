"""Pricing rules."""


def apply_discount(price: float, percent: float) -> float:
    """Return ``price`` reduced by ``percent`` percent (0-100)."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price - percent, 2)


def order_total(unit_price: float, quantity: int, discount_percent: float = 0) -> float:
    """Total for ``quantity`` items after applying the discount to each unit."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    return round(apply_discount(unit_price, discount_percent) * quantity, 2)
