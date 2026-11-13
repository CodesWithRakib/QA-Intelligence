from app.catalog import get_product, search_products


def test_get_existing_product():
    assert get_product(1).name == "Mechanical keyboard"


def test_search_matches_by_name():
    assert [product.id for product in search_products("hub")] == [2]
