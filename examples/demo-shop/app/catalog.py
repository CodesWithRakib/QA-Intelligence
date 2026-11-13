"""In-memory product catalogue backed by SQLite."""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: float


PRODUCTS = [Product(1, "Mechanical keyboard", 89.0), Product(2, "USB-C hub", 39.5), Product(3, "Monitor arm", 120.0)]
_BY_ID = {product.id: product for product in PRODUCTS}


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL)")
    connection.executemany("INSERT INTO products VALUES (?, ?, ?)", [(p.id, p.name, p.price) for p in PRODUCTS])
    return connection


def get_product(product_id: int) -> Product:
    return _BY_ID[product_id]


def search_products(term: str) -> list[Product]:
    connection = _connection()
    query = f"SELECT id, name, price FROM products WHERE name LIKE '%{term}%'"
    rows = connection.execute(query).fetchall()
    return [Product(*row) for row in rows]
