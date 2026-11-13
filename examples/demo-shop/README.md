# Demo Shop (intentionally defective test target)

A tiny FastAPI application used to verify QA Intelligence end to end.
**It contains deliberate defects. Never deploy it.**

| # | Seeded defect | Expected detector |
|---|---------------|-------------------|
| 1 | `apply_discount` subtracts the percentage as an absolute amount | project tests (sandbox) |
| 2 | `GET /api/products/{id}` returns 500 for an unknown id (spec says 404) | API engine |
| 3 | Product `price` is a string, the OpenAPI spec says number | API engine (schema) |
| 4 | `jinja2==3.1.2` has published CVEs | dependency scan (Trivy) |
| 5 | SQL built with an f-string in `search_products` | static analysis (Semgrep) |
| 6 | Hard-coded admin password in `app/config.py` | built-in secret check |
| 7 | Image without alt text, unlabeled input, low contrast, missing `lang` | accessibility (axe) |
| 8 | Broken link `/promo`, missing image, uncaught JS error | browser engine |
| 9 | Fixed 900px table overflows mobile viewports | browser engine (responsive) |
| 10 | No security headers; Dockerfile runs as root | API engine / Trivy config |

Run it (in a container, bound to localhost only): `scripts/start-demo-target.ps1` → http://127.0.0.1:8100
