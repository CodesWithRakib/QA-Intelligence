"""Demo Shop API and pages."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import catalog, pages
from app.pricing import order_total

app = FastAPI(title="Demo Shop", version="1.0.0")


class OrderRequest(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=100)
    discount_percent: float = Field(default=0, ge=0, le=100)


def _serialise(product: catalog.Product) -> dict[str, object]:
    return {"id": product.id, "name": product.name, "price": f"{product.price:.2f}"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/products")
def list_products() -> list[dict[str, object]]:
    return [_serialise(product) for product in catalog.PRODUCTS]


@app.get("/api/products/search")
def search(q: str = "") -> list[dict[str, object]]:
    return [_serialise(product) for product in catalog.search_products(q)]


@app.get("/api/products/{product_id}")
def get_product(product_id: int) -> dict[str, object]:
    return _serialise(catalog.get_product(product_id))


@app.post("/api/orders", status_code=201)
def create_order(order: OrderRequest) -> dict[str, object]:
    product = catalog.get_product(order.product_id)
    return {"product_id": product.id, "quantity": order.quantity,
            "total": order_total(product.price, order.quantity, order.discount_percent)}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return pages.home()


@app.get("/contact", response_class=HTMLResponse)
def contact() -> str:
    return pages.contact()
