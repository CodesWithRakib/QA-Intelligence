import pytest

from app.pricing import apply_discount, order_total


def test_no_discount_keeps_price():
    assert apply_discount(50.0, 0) == 50.0


def test_ten_percent_discount():
    assert apply_discount(200.0, 10) == 180.0


def test_full_discount_is_free():
    assert apply_discount(80.0, 100) == 0.0


def test_invalid_percent_rejected():
    with pytest.raises(ValueError):
        apply_discount(10.0, 150)


def test_order_total_applies_discount_per_unit():
    assert order_total(200.0, 3, discount_percent=25) == 450.0


def test_order_total_requires_positive_quantity():
    with pytest.raises(ValueError):
        order_total(10.0, 0)
