"""Server-rendered pages (with seeded accessibility and UX defects)."""

from jinja2 import Template

from app.catalog import PRODUCTS
from app.config import SHOP_NAME

_HOME = Template(
    """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ shop }}</title>
  <style>
    body { font-family: sans-serif; margin: 2rem; }
    .muted { color: #c9c9c9; background: #ffffff; }
    table { width: 900px; border-collapse: collapse; }
    td, th { border: 1px solid #ddd; padding: 8px; text-align: left; }
  </style>
</head>
<body>
  <h1>{{ shop }}</h1>
  <img src="/static/hero.png" width="320" height="120">
  <p class="muted">Quality gear for people who work at a desk.</p>
  <nav><a href="/contact">Contact</a> | <a href="/promo">Current promotion</a></nav>
  <table>
    <tr><th>Product</th><th>Price</th></tr>
    {% for product in products %}<tr><td>{{ product.name }}</td><td>{{ product.price }}</td></tr>{% endfor %}
  </table>
  <form action="/api/products/search" method="get">
    <input type="text" name="q" placeholder="Search products">
    <button type="submit">Search</button>
  </form>
  <script>renderRecommendations();</script>
</body>
</html>"""
)

_CONTACT = Template(
    """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contact - {{ shop }}</title></head>
<body>
  <main>
    <h1>Contact us</h1>
    <form>
      <label for="email">Email</label> <input id="email" type="email" name="email" required>
      <label for="message">Message</label> <textarea id="message" name="message" required></textarea>
      <button type="submit">Send</button>
    </form>
    <p><a href="/">Back to shop</a></p>
  </main>
</body>
</html>"""
)


def home() -> str:
    return _HOME.render(shop=SHOP_NAME, products=PRODUCTS)


def contact() -> str:
    return _CONTACT.render(shop=SHOP_NAME)
